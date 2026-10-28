import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { stripVTControlCharacters } from 'node:util';

export const DOCKER_ENVIRONMENT = [
  'PATH',
  'HOME',
  'DOCKER_HOST',
  'DOCKER_CONTEXT',
  'DOCKER_CONFIG',
  'TMPDIR',
];

export const diagnosticTail = (value, env = {}) => {
  let text = stripVTControlCharacters(String(value));
  const secrets = Object.entries(env)
    .filter(
      ([key, secret]) =>
        /TOKEN|KEY|PASSWORD|SECRET|DATABASE_URL|BROKER_URL|REDIS_URL/.test(key) && secret,
    )
    .map(([, secret]) => String(secret))
    .sort((a, b) => b.length - a.length);
  for (const secret of secrets) text = text.replaceAll(secret, '[REDACTED]');
  text = text
    .replace(/(Bearer\s+)\S+/gi, '$1[REDACTED]')
    .replace(/([?&#](?:ticket|token)=)[^&\s"']+/gi, '$1[REDACTED]')
    .replace(
      /(\b(?:Token:|\w*(?:TOKEN|KEY|PASSWORD|SECRET)\w*\s*[=:])\s*)[^\s"']+/gi,
      '$1[REDACTED]',
    )
    .replace(/:\/\/[^\s/@]+:[^\s/@]+@/g, '://[REDACTED]@');
  return text.split('\n').slice(-40).join('\n').slice(-4000).trim();
};

export const assertOwnedProject = (project) => {
  if (!/^taskbot-e2e-[a-z0-9]+-[a-z0-9]+$/.test(project)) {
    throw new Error('Refusing cleanup outside an owned acceptance project');
  }
};

export const acceptanceEnvironment = (source, { project, port, token = '' }) => ({
  ...Object.fromEntries(Object.entries(source).filter(([key]) => DOCKER_ENVIRONMENT.includes(key))),
  COMPOSE_PROJECT_NAME: project,
  ...(port === undefined ? {} : { WEB_PORT: String(port) }),
  POSTGRES_USER: 'AcceptanceUser',
  POSTGRES_PASSWORD: 'AcceptancePassword',
  POSTGRES_DB: 'AcceptanceDatabase',
  TELEGRAM_BOT_TOKEN: token,
  TASK_EXTRACTOR: 'heuristic',
  TRANSCRIPTION_PROVIDER: 'openai',
  OPENAI_API_KEY: '',
  ANTHROPIC_API_KEY: '',
});

export const runCommand = (
  command,
  args,
  {
    env = process.env,
    input,
    timeout = 120000,
    allowFailure = false,
    signal,
    encoding = 'utf8',
    label = command,
  } = {},
) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('Acceptance interrupted'));
      return;
    }
    const child = spawn(command, args, {
      env,
      detached: process.platform !== 'win32',
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    const stdout = [],
      stderr = [];
    let reason,
      bytes = 0;
    const kill = () => {
      try {
        process.kill(process.platform === 'win32' ? child.pid : -child.pid, 'SIGKILL');
      } catch (error) {
        if (error.code !== 'ESRCH') child.kill('SIGKILL');
      }
    };
    const interrupt = () => {
      reason = 'Acceptance interrupted';
      kill();
    };
    const timer = setTimeout(() => {
      reason = `${label} timed out`;
      kill();
    }, timeout);
    signal?.addEventListener('abort', interrupt, { once: true });
    for (const [stream, chunks] of [
      [child.stdout, stdout],
      [child.stderr, stderr],
    ]) {
      stream.on('data', (chunk) => {
        bytes += chunk.length;
        if (bytes > 32 * 1024 * 1024) {
          reason = `${label} exceeded its output limit`;
          kill();
        } else chunks.push(chunk);
      });
    }
    child.stdin.on('error', (error) => {
      if (error.code !== 'EPIPE') {
        reason = `${label} input failed`;
        kill();
      }
    });
    child.stdin.end(input);
    const finish = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', interrupt);
    };
    child.on('error', () => {
      finish();
      reject(new Error(`${label} could not start`));
    });
    child.on('close', (code) => {
      finish();
      const convert = (chunks) =>
        encoding === 'buffer' ? Buffer.concat(chunks) : Buffer.concat(chunks).toString(encoding);
      const result = { code, stdout: convert(stdout), stderr: convert(stderr) };
      if (reason || (code !== 0 && !allowFailure)) {
        const details = diagnosticTail(result.stderr, env);
        const message = diagnosticTail(reason ?? `${label} failed (exit ${code})`, env);
        reject(
          Object.assign(new Error(`${message}${details ? `\n${details}` : ''}`), {
            result: { code, stdout: diagnosticTail(result.stdout, env), stderr: details },
          }),
        );
      } else resolve(result);
    });
  });

export const withCleanup = async (action, cleanup, source = process) => {
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  source.on('SIGINT', interrupt);
  source.on('SIGTERM', interrupt);
  let failure;
  try {
    return await action(controller.signal);
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
      await cleanup();
    } catch (error) {
      if (!failure) throw error;
      console.error(`Cleanup failed: ${error.message}`);
    } finally {
      source.removeListener('SIGINT', interrupt);
      source.removeListener('SIGTERM', interrupt);
    }
  }
};

export const waitFor = async (label, probe, { timeout = 120000, interval = 500, signal } = {}) => {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new Error('Acceptance interrupted');
    const value = await probe();
    if (value) return value;
    await delay(interval, undefined, { signal });
  }
  throw Object.assign(new Error(`${label} timed out`), { code: 'WAIT_TIMEOUT' });
};

const freePort = () =>
  new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });

export const createStack = async (root, token) => {
  const project = `taskbot-e2e-${Date.now().toString(36)}-${randomBytes(4).toString('hex')}`;
  assertOwnedProject(project);
  const scratch = await mkdtemp(join(tmpdir(), `${project}-`));
  const env = acceptanceEnvironment(process.env, { project, token });
  const emptyEnv = join(scratch, 'empty.env');
  await writeFile(emptyEnv, '', { mode: 0o600 });
  const ctx = {
    root,
    project,
    scratch,
    port: undefined,
    env,
    envFile: emptyEnv,
    signal: undefined,
    builder: false,
    baseUrl: undefined,
    selectPort: async () => {
      ctx.port = await freePort();
      env.WEB_PORT = String(ctx.port);
      ctx.baseUrl = `http://127.0.0.1:${ctx.port}`;
    },
    commands: [],
    measurements: {},
    memoryLimits: {},
    compose: (args, options = {}) =>
      ctx.docker(
        [
          'compose',
          '--project-name',
          project,
          '--project-directory',
          root,
          '--file',
          join(root, 'docker-compose.yml'),
          '--env-file',
          ctx.envFile,
          ...(ctx.whisper ? ['--profile', 'whisper'] : []),
          ...args,
        ],
        {
          label: `docker compose -p ${project} ${(args.includes('python') && args.at(-2) === '-c' ? [...args.slice(0, -1), '<inline Python probe>'] : args).map((arg) => (/^[\w./:@=-]+$/.test(arg) ? arg : `'${arg.replaceAll("'", "'\\''")}'`)).join(' ')}`,
          ...options,
        },
      ),
    docker: (args, { env: extra = {}, label, ...options } = {}) => {
      const commandLabel =
        label ??
        `docker ${args[0]} ${args[0] === 'compose' ? (args.find((arg) => ['up', 'down', 'exec', 'run', 'stop', 'start', 'restart', 'rm', 'build', 'logs', 'ps'].includes(arg)) ?? '') : (args[1] ?? '')}`;
      if (!ctx.commands.includes(commandLabel)) {
        ctx.commands.push(commandLabel);
        console.log(`$ ${diagnosticTail(commandLabel, env)}`);
      }
      return runCommand('docker', args, {
        env: { ...env, ...extra },
        signal: ctx.signal,
        label: commandLabel,
        ...options,
      });
    },
    id: async (service) => (await ctx.compose(['ps', '--all', '--quiet', service])).stdout.trim(),
    inspect: async (ids) => JSON.parse((await ctx.docker(['inspect', ...ids])).stdout),
    waitHealthy: async (service, timeout = 120000) => {
      try {
        return await waitFor(
          `${service} health`,
          async () => {
            const id = await ctx.id(service);
            if (!id) return false;
            const [item] = await ctx.inspect([id]);
            return item.State.Health?.Status === 'healthy';
          },
          { timeout, signal: ctx.signal, interval: 1000 },
        );
      } catch (error) {
        if (error.code !== 'WAIT_TIMEOUT') throw error;
        let details = 'Service logs could not be read';
        try {
          const logs = await ctx.compose(['logs', '--no-color', '--tail', '40', service], {
            timeout: 10000,
            allowFailure: true,
          });
          details =
            diagnosticTail(`${logs.stdout}\n${logs.stderr}`, env) || 'No service log output';
        } catch {
          /* Preserve the health failure if log retrieval also fails. */
        }
        throw new Error(`${service} health timed out\n${details}`);
      }
    },
    capture: async () => {
      const ids = (await ctx.compose(['ps', '--all', '--quiet'])).stdout
        .trim()
        .split(/\s+/)
        .filter(Boolean);
      return ids.length ? ctx.inspect(ids) : [];
    },
    cleanup: async () => {
      assertOwnedProject(project);
      ctx.signal = undefined;
      const failures = [];
      const attempt = async (operation) => {
        try {
          return await operation();
        } catch (error) {
          failures.push(error);
          return { stdout: '', code: 1 };
        }
      };
      await attempt(() =>
        ctx.compose(
          ['down', '--volumes', '--rmi', 'local', '--remove-orphans', '--timeout', '15'],
          { timeout: 180000 },
        ),
      );
      // A killed Compose client or a one-off probe can leave labeled containers.
      const remaining = await attempt(() =>
        ctx.docker([
          'ps',
          '--all',
          '--quiet',
          '--filter',
          `label=com.docker.compose.project=${project}`,
        ]),
      );
      const ids = remaining.stdout.trim().split(/\s+/).filter(Boolean);
      if (ids.length) await attempt(() => ctx.docker(['rm', '--force', ...ids]));
      for (const suffix of ['postgres-data', 'redis-data', 'whisper-models']) {
        await attempt(() =>
          ctx.docker(['volume', 'rm', `${project}_${suffix}`], { allowFailure: true }),
        );
      }
      await attempt(() =>
        ctx.docker(['network', 'rm', `${project}_default`], { allowFailure: true }),
      );
      // Compose rmi local deliberately preserves explicitly named images; these tags are ours.
      const images = ['backend', 'web', 'whisper'].map((role) => `${project}-${role}`);
      await attempt(() => ctx.docker(['image', 'rm', ...images], { allowFailure: true }));
      if (ctx.builder) {
        await attempt(() =>
          ctx.docker(['buildx', 'rm', '--force', project], { allowFailure: true }),
        );
        const builder = await attempt(() =>
          ctx.docker(['buildx', 'inspect', project], { allowFailure: true }),
        );
        if (builder.code === 0) failures.push(new Error('Owned acceptance builder remains'));
      }
      await rm(scratch, { recursive: true, force: true });
      const final = await attempt(() =>
        ctx.docker([
          'ps',
          '--all',
          '--quiet',
          '--filter',
          `label=com.docker.compose.project=${project}`,
        ]),
      );
      if (final.stdout.trim()) failures.push(new Error('Owned acceptance containers remain'));
      for (const kind of ['volume', 'network']) {
        const listed = await attempt(() =>
          ctx.docker([
            kind,
            'ls',
            '--filter',
            `label=com.docker.compose.project=${project}`,
            '--quiet',
          ]),
        );
        if (listed.stdout.trim()) failures.push(new Error(`Owned acceptance ${kind}s remain`));
      }
      const tagged = await attempt(() =>
        ctx.docker(['image', 'inspect', '--format', '{{.Id}}', ...images], { allowFailure: true }),
      );
      if (tagged.stdout.trim()) failures.push(new Error('Owned acceptance image tags remain'));
      if (failures.length)
        throw new AggregateError(failures, 'Acceptance cleanup did not finish cleanly');
    },
  };
  return ctx;
};

export const assertNormalContainers = (items) => {
  for (const item of items) {
    const name = item.Config.Labels['com.docker.compose.service'];
    assert.equal(item.State.OOMKilled, false, `${name} OOM`);
    assert.equal(item.RestartCount, 0, `${name} restarted`);
    if (name === 'migrate') assert.equal(item.State.ExitCode, 0, 'migration failed');
    else assert.equal(item.State.Health?.Status, 'healthy', `${name} unhealthy`);
  }
};
