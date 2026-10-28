import assert from 'node:assert/strict';
import { copyFile, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { parseEnv } from 'node:util';
import { isMain } from './lib/entrypoint.mjs';
import { REPOSITORY_ROOT } from './lib/env.mjs';
import { checkReadmeCommands } from './lib/readme-commands.mjs';
import { checkImagePins, checkSharedConfig } from './lib/stack-config.mjs';
import { checkSeedLink, checkStreamRestart, checkWeb, readTasks } from './lib/stack-http.mjs';
import {
  assertNormalContainers,
  createStack,
  runCommand,
  waitFor,
  withCleanup,
} from './lib/stack-runtime.mjs';
import { checkWhisper } from './lib/stack-whisper.mjs';
import {
  checkChildLifecycle,
  checkHardeningAndBudgets,
  checkWorkerLayout,
  checkWorkerTasks,
} from './lib/stack-workers.mjs';

const SERVICES = [
  'postgres',
  'redis',
  'migrate',
  'api',
  'worker-transcribe',
  'worker-notify',
  'web',
];
const fingerprint = (items) =>
  Object.fromEntries(
    items
      .filter((item) => item.Config.Labels['com.docker.compose.service'] !== 'migrate')
      .map((item) => [
        item.Config.Labels['com.docker.compose.service'],
        { id: item.Id, started: item.State.StartedAt },
      ]),
  );
const row = (name, diagnostic) => console.log(`${name} ${JSON.stringify(diagnostic)}`);

const assertNoOtherPoller = async (ctx, token) => {
  const { stdout } = await ctx.docker(['ps', '--quiet']);
  const ids = stdout.trim().split(/\s+/).filter(Boolean);
  const containers = ids.length ? await ctx.inspect(ids) : [];
  for (const item of containers) {
    const sameToken = item.Config.Env?.includes(`TELEGRAM_BOT_TOKEN=${token}`);
    const bot = [...(item.Config.Entrypoint ?? []), ...(item.Config.Cmd ?? [])]
      .join(' ')
      .includes('taskbot.entrypoints.bot');
    assert.ok(!(sameToken && bot), 'Stop the other Docker poller before token-enabled acceptance');
  }
  const processes = await runCommand('pgrep', ['-f', 'taskbot.entrypoints.bot'], {
    allowFailure: true,
  });
  assert.equal(
    processes.stdout.trim(),
    '',
    'Stop the development bot before token-enabled acceptance',
  );
};

const sharedSnapshot = async (ctx) => {
  const { stdout } = await ctx.docker([
    'ps',
    '--quiet',
    '--filter',
    'label=com.docker.compose.project=taskbot',
  ]);
  const ids = stdout.trim().split(/\s+/).filter(Boolean);
  return ids.length ? fingerprint(await ctx.inspect(ids)) : {};
};

export const prepareImages = async (ctx, reuse) => {
  if (reuse) {
    for (const name of ['backend', 'web', 'whisper'])
      await ctx.docker(['tag', `taskbot-s8-${name}:local`, `${ctx.project}-${name}`]);
  } else {
    // A timed-out client can leave a builder after Docker accepted its creation.
    ctx.builder = true;
    await ctx.docker(['buildx', 'create', '--name', ctx.project, '--driver', 'docker-container']);
    for (const service of ['api', 'web', 'whisper']) {
      await ctx.compose(['build', '--builder', ctx.project, service], { timeout: 1200000 });
    }
  }
  const images = await ctx.docker([
    'image',
    'inspect',
    ...['backend', 'web', 'whisper'].map((name) => `${ctx.project}-${name}`),
  ]);
  return Object.fromEntries(
    JSON.parse(images.stdout).map((item) => [
      item.RepoTags.find((tag) => tag.startsWith(ctx.project))
        .split('-')
        .at(-1)
        .split(':')[0],
      { bytes: item.Size, architecture: item.Architecture },
    ]),
  );
};

const start = (ctx, bot = false) =>
  ctx.compose(
    [
      'up',
      '--no-build',
      '-d',
      '--wait',
      '--wait-timeout',
      '180',
      ...SERVICES,
      ...(bot ? ['bot'] : []),
    ],
    { timeout: 210000 },
  );

const seed = async (ctx) => {
  const result = await ctx.compose([
    'run',
    '--rm',
    'seed',
    '800000001',
    'Call the acceptance team tomorrow.',
  ]);
  const token = result.stdout.match(/^Token: (\S+)$/m)?.[1];
  const link = result.stdout.match(/^Link: (\S+)$/m)?.[1];
  assert.ok(token && link, 'seed did not produce credentials');
  checkSeedLink(ctx.baseUrl, link, token);
  const tasks = await readTasks(ctx.baseUrl, token);
  assert.ok(tasks.items.some((item) => item.source_text === 'Call the acceptance team tomorrow.'));
  return { token, tasks };
};

const stoppedNormally = async (ctx, service, budget) => {
  const start = Date.now();
  await ctx.compose(['stop', service], { timeout: budget + 10000 });
  const [item] = await ctx.inspect([await ctx.id(service)]);
  assert.equal(item.State.ExitCode, 0, `${service} stop status`);
  assert.ok(Date.now() - start < budget, `${service} exceeded grace`);
  return { exitCode: item.State.ExitCode, elapsedMs: Date.now() - start };
};

const measureTransient = async (ctx) => {
  for (const [service, command] of [
    ['migrate', 'alembic upgrade head'],
    ['seed', 'python -m taskbot.entrypoints.seed 800000002 "Record the resource measurement."'],
  ]) {
    const name = `${ctx.project}-measure-${service}`;
    try {
      await ctx.compose([
        'run',
        '--detach',
        '--no-deps',
        '--name',
        name,
        '--entrypoint',
        'sh',
        service,
        '-c',
        `${command}; result=$?; echo APPLICATION_UID; id -u; echo MEMORY_PEAK; cat /sys/fs/cgroup/memory.peak; exit "$result"`,
      ]);
      const stopped = await ctx.docker(['wait', name]);
      assert.equal(Number(stopped.stdout.trim()), 0, `${service} measurement failed`);
      const result = await ctx.docker(['logs', name]);
      const peak = Number(result.stdout.match(/MEMORY_PEAK\s+(\d+)/)?.[1]);
      const uid = Number(result.stdout.match(/APPLICATION_UID\s+(\d+)/)?.[1]);
      assert.ok(peak > 0 && uid > 0, `${service} measurement missing`);
      const [item] = await ctx.inspect([name]);
      ctx.measurements[service] = peak;
      ctx.memoryLimits[service] = item.HostConfig.Memory;
    } finally {
      await ctx.docker(['rm', '--force', name], { allowFailure: true, signal: undefined });
    }
  }
};

const restore = async (ctx, token, expected, bot) => {
  const dumpCommand = 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"';
  const restoreCommand = 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"';
  const dump = await ctx.compose(['exec', '-T', 'postgres', 'sh', '-c', dumpCommand], {
    encoding: 'buffer',
  });
  const path = join(ctx.scratch, 'backup.dump');
  await writeFile(path, dump.stdout, { mode: 0o600 });
  assert.ok(dump.stdout.length > 0);
  await ctx.compose(['down', '--volumes', '--timeout', '15'], { timeout: 150000 });
  await start(ctx, bot);
  await ctx.compose(['stop', 'api', 'bot', 'worker-transcribe', 'worker-notify'], {
    timeout: 150000,
  });
  await ctx.compose(['exec', '-T', 'postgres', 'sh', '-c', restoreCommand], {
    input: await readFile(path),
  });
  await start(ctx, bot);
  const actual = await readTasks(ctx.baseUrl, token);
  assert.deepEqual(actual.items, expected.items);
  return {
    restoredTasks: actual.items.length,
    dumpBytes: dump.stdout.length,
    nonDefaultCredentials: true,
  };
};

export const readAcceptanceToken = async (root, keyless = true) => {
  if (keyless) return '';
  let token = '';
  try {
    token = parseEnv(await readFile(join(root, '.env'), 'utf8')).TELEGRAM_BOT_TOKEN ?? '';
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  assert.ok(token, 'Token-enabled acceptance requires TELEGRAM_BOT_TOKEN in the untracked .env');
  return token;
};

export const acceptance = async ({
  root = REPOSITORY_ROOT,
  reuse = false,
  keyless = true,
} = {}) => {
  const token = await readAcceptanceToken(root, keyless);
  const ctx = await createStack(root, token);
  return withCleanup(async (signal) => {
    ctx.signal = signal;
    const exampleEnv = join(ctx.scratch, 'example.env');
    await copyFile(join(root, '.env.example'), exampleEnv);
    ctx.envFile = exampleEnv;
    const config = JSON.parse((await ctx.compose(['config', '--format', 'json'])).stdout);
    const readmeCommands = await checkReadmeCommands(root, config.services);
    for (const command of readmeCommands) console.log(`README VERIFIED ${command}`);
    if (token) await assertNoOtherPoller(ctx, token);
    const shared = await sharedSnapshot(ctx);
    const report = {
      project: ctx.project,
      readmeCommands,
      sharedConfig: await checkSharedConfig(ctx),
      pins: await checkImagePins(ctx),
      images: await prepareImages(ctx, reuse),
      rows: {},
      diagnostics: {},
    };
    // Keyless rows use the same production settings, with no provider key or bot token.
    await ctx.selectPort();
    await ctx.compose(['up', '--no-build', '-d', '--wait', '--wait-timeout', '180', ...SERVICES], {
      env: { TELEGRAM_BOT_TOKEN: '' },
      timeout: 210000,
    });
    assert.equal(
      (await ctx.capture()).filter((item) => item.State.Health?.Status === 'healthy').length,
      6,
    );
    const seeded = await seed(ctx);
    report.rows.R2 = { healthyServices: 6, seededTasks: seeded.tasks.items.length };
    row('R2', report.rows.R2);
    report.diagnostics.web = await checkWeb(ctx.baseUrl);
    report.rows.R3 = await checkStreamRestart(ctx.baseUrl, seeded.token, async () => {
      await ctx.compose(['restart', 'api']);
      await ctx.waitHealthy('api', 30000);
    });
    row('R3', report.rows.R3);
    report.diagnostics.layout = await checkWorkerLayout(ctx);
    report.diagnostics.workerTasks = await checkWorkerTasks(ctx);
    report.diagnostics.normalResources = await checkHardeningAndBudgets(ctx);
    await measureTransient(ctx);
    row('A10-A12', { ...report.diagnostics.normalResources, memoryPeaks: ctx.measurements });
    assertNormalContainers(await ctx.capture());

    if (token) {
      await start(ctx, true);
      const stableStart = Date.now();
      await waitFor(
        'two-minute stable window',
        async () => {
          assertNormalContainers(await ctx.capture());
          return Date.now() - stableStart >= 120000;
        },
        { timeout: 135000, interval: 5000, signal },
      );
      report.rows.R1 = { healthyServices: 7, stableSeconds: 120, restarts: 0, oomKills: 0 };
      report.diagnostics.botResources = await checkHardeningAndBudgets(ctx);
      report.diagnostics.botStop = await stoppedNormally(ctx, 'bot', 40000);
      const logs = await ctx.compose(['logs', '--no-color', 'bot']);
      assert.ok(logs.stdout.includes('Polling stopped'), 'bot did not log polling stop');
      assert.ok(
        logs.stdout.includes('Producer threads drained and closed'),
        'bot producer did not drain',
      );
      await start(ctx, true);
    } else
      report.rows.R1 = {
        status: 'DEFERRED owner final-main check',
        checks: ['A1/R1 Telegram startup', 'A6 bot stop/drain', 'bot memory measurement'],
      };
    row('R1', report.rows.R1);

    report.diagnostics.idleStop = await stoppedNormally(ctx, 'worker-transcribe', 15000);
    await start(ctx, Boolean(token));
    const first = await ctx.capture();
    const before = fingerprint(first);
    await start(ctx, Boolean(token));
    const second = await ctx.capture();
    assertNormalContainers(second);
    assert.deepEqual(fingerprint(second), before, 'idempotent up recreated containers');
    const migration = (items) =>
      items.find((item) => item.Config.Labels['com.docker.compose.service'] === 'migrate');
    assert.notEqual(
      migration(second).State.StartedAt,
      migration(first).State.StartedAt,
      'migration was not rerun',
    );
    await ctx.compose(['down', '--timeout', '15'], { timeout: 150000 });
    await start(ctx, Boolean(token));
    assert.deepEqual((await readTasks(ctx.baseUrl, seeded.token)).items, seeded.tasks.items);
    report.rows.R4 = {
      persistedTasks: seeded.tasks.items.length,
      idempotent: true,
      migrationReran: true,
    };
    row('R4', report.rows.R4);
    report.rows.R5 = await restore(ctx, seeded.token, seeded.tasks, Boolean(token));
    row('R5', report.rows.R5);
    report.rows.R7 = {
      ...report.diagnostics.workerTasks,
      lifecycle: await checkChildLifecycle(ctx),
    };
    row('R7', report.rows.R7);
    ctx.whisper = true;
    report.rows.R6 = await checkWhisper(ctx);
    row('R6', report.rows.R6);
    report.diagnostics.finalResources = await checkHardeningAndBudgets(ctx);
    assertNormalContainers(await ctx.capture());
    assert.deepEqual(
      await sharedSnapshot(ctx),
      shared,
      'shared containers changed during acceptance',
    );
    report.memoryPeaks = ctx.measurements;
    report.memoryLimits = ctx.memoryLimits;
    console.log(`ACCEPTANCE ${JSON.stringify(report)}`);
    for (const [service, peak] of Object.entries(ctx.measurements)) {
      assert.ok(
        ctx.memoryLimits[service] >= 2 * peak,
        `${service} needs a memory limit of at least twice its peak (${peak} bytes)`,
      );
    }
    return report;
  }, ctx.cleanup);
};

if (isMain(import.meta.url)) {
  const args = process.argv.slice(2);
  if (
    args.some((arg) => !['--reuse-images', '--keyless', '--with-telegram'].includes(arg)) ||
    (args.includes('--keyless') && args.includes('--with-telegram'))
  ) {
    console.error(
      'Usage: node scripts/stack-acceptance.mjs [--reuse-images] [--keyless | --with-telegram]',
    );
    process.exitCode = 2;
  } else {
    console.log(
      'Isolated Docker acceptance: allow 5–15 minutes; model download and CPU inference run serially.',
    );
    acceptance({
      reuse: args.includes('--reuse-images'),
      keyless: !args.includes('--with-telegram'),
    }).catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
  }
}
