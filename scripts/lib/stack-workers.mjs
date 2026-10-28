import assert from 'node:assert/strict';
import { join } from 'node:path';
import { waitFor } from './stack-runtime.mjs';

const layoutScript = `import glob,json,os
from pathlib import Path
rows=[]
for path in glob.glob('/proc/[0-9]*'):
 try:
  command=Path(path+'/cmdline').read_bytes().replace(b'\\0',b' ').decode()
  status=Path(path+'/status').read_text()
  parent=int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
  if parent == 1 and 'celery' in command: rows.append({'pid':int(path.rsplit('/',1)[1])})
 except (PermissionError,ProcessLookupError,FileNotFoundError): pass
print(json.dumps({'init':Path('/proc/1/comm').read_text().strip(),'workers':rows}))`;

export const checkWorkerLayout = async (ctx) => {
  const result = {};
  for (const service of ['worker-transcribe', 'worker-notify']) {
    const { stdout } = await ctx.compose(['exec', '-T', service, 'python', '-c', layoutScript]);
    const row = JSON.parse(stdout);
    assert.equal(row.init, 'docker-init');
    assert.equal(row.workers.length, 1, `${service} main process missing`);
    const main = row.workers[0].pid;
    assert.ok(main > 1);
    const { stdout: command } = await ctx.compose([
      'exec',
      '-T',
      service,
      'python',
      '-c',
      `from pathlib import Path; print(Path('/proc/${main}/cmdline').read_bytes().replace(b'\\0',b' ').decode())`,
    ]);
    assert.ok(command.includes('celery'), `${service} recorded main is not Celery`);
    result[service] = { init: row.init, mainPid: main };
  }
  return result;
};

export const checkWorkerTasks = async (ctx) => {
  // Voice job primary keys are PostgreSQL INTEGER, rather than Telegram's BIGINT.
  const missing = 2147483000;
  await ctx.compose([
    'exec',
    '-T',
    'worker-transcribe',
    'celery',
    '-A',
    'taskbot.entrypoints.worker',
    'call',
    'transcribe_voice_job',
    '--queue',
    'transcription',
    '--kwargs',
    JSON.stringify({ voice_job_id: missing }),
  ]);
  await waitFor(
    'transcription child job',
    async () => {
      const { stdout } = await ctx.compose(['logs', '--no-color', 'worker-transcribe']);
      assert.ok(
        !stdout.includes('Transcription job failed'),
        stdout.match(/Transcription job failed \([A-Za-z]+\)/)?.[0] ?? 'transcription failed',
      );
      return stdout.includes(`Voice job missing: ${missing}`);
    },
    { signal: ctx.signal },
  );
  const sweeps = await waitFor(
    'startup and beat child sweeps',
    async () => {
      const { stdout } = await ctx.compose(['logs', '--timestamps', '--no-color', 'worker-notify']);
      assert.ok(!stdout.includes('Voice recovery job failed'));
      const lines = stdout
        .split('\n')
        .filter((line) => line.includes('Voice recovery job completed'));
      const times = lines.map((line) => Date.parse(line.split('|')[1]?.trim().split(' ')[0]));
      return times.length >= 2 && times.at(-1) - times[0] >= 59000 ? times : false;
    },
    { timeout: 150000, interval: 2000, signal: ctx.signal },
  );
  return {
    missingJobRefused: true,
    sweeps: sweeps.length,
    sweepSeparationMs: sweeps.at(-1) - sweeps[0],
  };
};

export const checkChildLifecycle = async (ctx) => {
  const namespace = `acceptance_${ctx.project.replaceAll('-', '_')}:`;
  const mount = `${join(ctx.root, 'backend/tests')}:/app/tests:ro`;
  const prefix = [
    'run',
    '--detach',
    '--no-deps',
    '--volume',
    mount,
    '--env',
    'PYTHONPATH=/app',
    '--env',
    `REDIS_NAMESPACE=${namespace}`,
  ];
  const failureName = `${ctx.project}-failed-child`;
  const warmName = `${ctx.project}-warm-child`;
  const notifyName = `${ctx.project}-notify-layout`;
  const layout = {};
  const readLayout = async (name, count) =>
    waitFor(
      'pool-child layout receipt',
      async () => {
        const logs = await ctx.docker(['logs', name]);
        const [item] = await ctx.inspect([name]);
        assert.ok(item.State.Running, `Layout probe stopped (exit ${item.State.ExitCode})`);
        const rows = [
          ...(logs.stdout + logs.stderr).matchAll(/STACK_PROCESS_LAYOUT (\{[^\n]+\})/g),
        ].map((match) => JSON.parse(match[1]));
        if (rows.length < count) return false;
        for (const row of rows) {
          assert.equal(row.init, 'docker-init');
          assert.ok(row.uid > 0);
          assert.ok(row.parent > 1);
          assert.equal(row.parent, row.recorded_main);
        }
        return rows;
      },
      { signal: ctx.signal },
    );
  let failure;
  try {
    await ctx.compose([
      ...prefix,
      '--name',
      notifyName,
      'worker-notify',
      'python',
      '-m',
      'taskbot.entrypoints.worker',
      '--include',
      'tests.stack_worker_probe',
    ]);
    layout.notify = await readLayout(notifyName, 2);
    await ctx.docker(['stop', '--time', '15', notifyName]);
    const args = [
      ...prefix,
      '--name',
      failureName,
      '--env',
      'TASKBOT_TEST_FAIL_CHILD_RUNTIME=1',
      'worker-transcribe',
      'python',
      '-m',
      'taskbot.entrypoints.worker',
      '--include',
      'tests.worker_tasks',
    ];
    await ctx.compose(args);
    const start = Date.now();
    const { stdout } = await ctx.docker(['wait', failureName], { timeout: 120000 });
    assert.equal(Number(stdout.trim()), 0, 'fatal child did not warm-stop the main');
    const failureLogs = await ctx.docker(['logs', failureName]);
    assert.ok((failureLogs.stdout + failureLogs.stderr).includes('Worker child runtime failed'));
    failure = { stopped: true, exitCode: 0, elapsedMs: Date.now() - start };
    // Prove unless-stopped restarts the same fatal path, separately from normal health checks.
    await ctx.docker(['update', '--restart', 'unless-stopped', failureName]);
    await ctx.docker(['start', failureName]);
    await waitFor(
      'fatal-child automatic restart',
      async () => {
        const [item] = await ctx.inspect([failureName]);
        return item.RestartCount > 0;
      },
      { timeout: 120000, signal: ctx.signal },
    );
    await ctx.docker(['stop', '--time', '15', failureName]);
    failure.restarted = true;

    await ctx.compose([
      ...prefix,
      '--name',
      warmName,
      'worker-transcribe',
      'python',
      '-m',
      'taskbot.entrypoints.worker',
      '--include',
      'tests.worker_tasks,tests.stack_worker_probe',
    ]);
    await waitFor(
      'one-off worker readiness',
      async () => {
        const r = await ctx.docker(['logs', warmName]);
        return (r.stdout + r.stderr).includes('Worker ready role=transcribe');
      },
      { signal: ctx.signal },
    );
    layout.transcribe = await readLayout(warmName, 1);
    await ctx.docker([
      'exec',
      warmName,
      'celery',
      '-A',
      'taskbot.entrypoints.worker',
      'call',
      's3test.sleep',
      '--queue',
      'transcription',
      '--kwargs',
      JSON.stringify({ seconds: 10, marker: 'warm', log: '/tmp/warm.jsonl' }),
    ]);
    await waitFor(
      'active warm task',
      async () => {
        const r = await ctx.docker(['exec', warmName, 'cat', '/tmp/warm.jsonl'], {
          allowFailure: true,
        });
        return r.stdout.includes('"event": "start"');
      },
      { signal: ctx.signal },
    );
    const warmStart = Date.now();
    await ctx.docker(['stop', '--time', '30', warmName], { timeout: 40000 });
    const [item] = await ctx.inspect([warmName]);
    assert.equal(item.State.ExitCode, 0);
    // tmpfs disappears at stop; Celery logs task success before its warm exit.
    const logs = await ctx.docker(['logs', warmName]);
    assert.ok(
      (logs.stdout + logs.stderr).includes('s3test.sleep[') &&
        (logs.stdout + logs.stderr).includes('succeeded'),
      'active task did not finish before exit',
    );
    return {
      layout,
      failure,
      warmShutdown: { exitCode: 0, activeTaskCompleted: true, elapsedMs: Date.now() - warmStart },
    };
  } finally {
    await ctx.docker(['rm', '--force', failureName, warmName, notifyName], {
      allowFailure: true,
      signal: undefined,
    });
    // These keys belong exclusively to the one-off lifecycle probes.
    if (!ctx.signal?.aborted)
      for (const database of ['0', '1']) {
        const { stdout } = await ctx.compose([
          'exec',
          '-T',
          'redis',
          'redis-cli',
          '-n',
          database,
          '--scan',
          '--pattern',
          `${namespace}*`,
        ]);
        const keys = stdout.trim().split('\n').filter(Boolean);
        if (keys.length)
          await ctx.compose(['exec', '-T', 'redis', 'redis-cli', '-n', database, 'DEL', ...keys]);
      }
  }
};

export const checkHardeningAndBudgets = async (ctx) => {
  const items = await ctx.capture();
  const hardening = {},
    addresses = {};
  for (const item of items) {
    const service = item.Config.Labels['com.docker.compose.service'];
    const host = item.HostConfig;
    assert.equal(
      host.RestartPolicy?.Name,
      service === 'migrate' ? 'no' : 'unless-stopped',
      `${service} restart policy`,
    );
    assert.equal(host.ReadonlyRootfs, true, `${service} writable root`);
    assert.ok(host.SecurityOpt.includes('no-new-privileges:true'));
    assert.ok(host.CapDrop.includes('ALL'));
    assert.ok(!host.CapAdd?.length);
    assert.ok(host.Memory > 0);
    assert.equal(host.LogConfig.Type, 'json-file');
    assert.deepEqual(host.LogConfig.Config, { 'max-file': '3', 'max-size': '10m' });
    assert.ok(item.Config.User && !['0', 'root', '0:0'].includes(item.Config.User));
    if (service !== 'web')
      assert.ok(
        !Object.values(item.NetworkSettings.Ports ?? {}).some(Boolean),
        `${service} host port`,
      );
    else
      assert.deepEqual(item.HostConfig.PortBindings['8080/tcp'], [
        { HostIp: '127.0.0.1', HostPort: String(ctx.port) },
      ]);
    for (const network of Object.values(item.NetworkSettings.Networks))
      addresses[network.IPAddress] = service;
    if (item.State.Running) {
      const id = item.Id;
      const { stdout } = await ctx.docker(['exec', id, 'cat', '/sys/fs/cgroup/memory.peak']);
      const peak = Number(stdout.trim());
      ctx.measurements[service] = Math.max(peak, ctx.measurements[service] ?? 0);
      let applicationPid = 1;
      if (host.Init) {
        const children = await ctx.docker(['exec', id, 'cat', '/proc/1/task/1/children']);
        applicationPid = Number(children.stdout.trim().split(/\s+/)[0]);
        assert.ok(applicationPid > 1, `${service} application child missing`);
      }
      const status = await ctx.docker(['exec', id, 'cat', `/proc/${applicationPid}/status`]);
      const uid = Number(status.stdout.match(/^Uid:\s+(\d+)/m)?.[1]);
      assert.ok(uid > 0, `${service} root process`);
      ctx.memoryLimits[service] = host.Memory;
      hardening[service] = { uid, limitBytes: host.Memory, peakBytes: peak };
    }
  }
  const { stdout } = await ctx.compose([
    'exec',
    '-T',
    'postgres',
    'sh',
    '-c',
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT client_addr, count(*) FROM pg_stat_activity WHERE backend_type = \'client backend\' AND client_addr IS NOT NULL GROUP BY client_addr"',
  ]);
  const sql = {};
  for (const row of stdout.trim().split('\n').filter(Boolean)) {
    const [address, count] = row.split('|');
    const service = addresses[address];
    assert.ok(service, 'unknown SQL client in isolated stack');
    sql[service] = Number(count);
  }
  const budgets = { api: 5, bot: 5, 'worker-transcribe': 5, 'worker-notify': 10 };
  for (const [service, count] of Object.entries(sql))
    assert.ok(count <= (budgets[service] ?? 0), `${service} SQL budget`);
  assert.ok(Object.values(sql).reduce((sum, count) => sum + count, 0) <= 25);
  const clients = await ctx.compose(['exec', '-T', 'redis', 'redis-cli', 'CLIENT', 'LIST']);
  const redisClients = clients.stdout.trim().split('\n').filter(Boolean).length;
  return { containers: hardening, sqlConnections: sql, redisClients };
};
