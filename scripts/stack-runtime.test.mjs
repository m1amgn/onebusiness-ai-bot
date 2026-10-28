import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { prepareImages, readAcceptanceToken } from './stack-acceptance.mjs';
import {
  acceptanceEnvironment,
  assertOwnedProject,
  createStack,
  runCommand,
  withCleanup,
} from './lib/stack-runtime.mjs';

test('acceptance ignores dotenv credentials by default and requires explicit Telegram opt-in', async () => {
  const root = await mkdtemp(join(tmpdir(), 'taskbot-acceptance-token-'));
  try {
    await writeFile(join(root, '.env'), 'TELEGRAM_BOT_TOKEN=fixture-token\n');
    assert.equal(await readAcceptanceToken(root), '');
    assert.equal(await readAcceptanceToken(root, false), 'fixture-token');
    await writeFile(join(root, '.env'), 'TELEGRAM_BOT_TOKEN=\n');
    await assert.rejects(readAcceptanceToken(root, false), /requires TELEGRAM_BOT_TOKEN/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('a builder created before a Docker client failure is still removed', async () => {
  const ctx = await createStack('/repository', '');
  let builderExists = false;
  ctx.docker = async (args) => {
    if (args[0] === 'buildx' && args[1] === 'create') {
      builderExists = true;
      throw new Error('client disconnected after create');
    }
    if (args[0] === 'buildx' && args[1] === 'rm') builderExists = false;
    if (args[0] === 'buildx' && args[1] === 'inspect')
      return { code: builderExists ? 0 : 1, stdout: '', stderr: '' };
    return { code: 0, stdout: '', stderr: '' };
  };
  await assert.rejects(
    withCleanup(() => prepareImages(ctx, false), ctx.cleanup),
    /client disconnected/,
  );
  assert.equal(builderExists, false);
});

test('fresh image preparation builds one target at a time on its owned builder', async () => {
  const targets = [];
  const project = 'taskbot-e2e-build-test';
  const ctx = {
    project,
    docker: async () => ({
      stdout: JSON.stringify(
        ['backend', 'web', 'whisper'].map((name) => ({
          RepoTags: [`${project}-${name}:latest`],
          Size: 1,
          Architecture: 'arm64',
        })),
      ),
    }),
    compose: async (args) => {
      assert.deepEqual(args.slice(0, 3), ['build', '--builder', project]);
      assert.equal(args.length, 4, 'one build invocation must select one target');
      targets.push(args[3]);
    },
  };
  await prepareImages(ctx, false);
  assert.deepEqual(targets, ['api', 'web', 'whisper']);
});

test('normal startup leaves whisper inactive; cleanup covers owned leftovers', async () => {
  const ctx = await createStack('/repository', '');
  const calls = [];
  ctx.docker = async (args) => {
    calls.push(args);
    return { code: 0, stdout: '', stderr: '' };
  };
  try {
    await ctx.compose(['up', 'api']);
    assert.equal(calls[0].includes('--profile'), false);
    ctx.whisper = true;
    await ctx.compose(['up', 'whisper']);
    assert.ok(calls[1].includes('--profile'));
  } finally {
    await ctx.cleanup();
  }
  assert.ok(
    calls.some(
      (args) =>
        args.includes('--filter') &&
        args.includes(`label=com.docker.compose.project=${ctx.project}`),
    ),
  );
});

test('acceptance only inherits Docker routing and explicitly chooses keyless providers', () => {
  const environment = acceptanceEnvironment(
    {
      PATH: '/bin',
      HOME: '/home/test',
      DOCKER_CONTEXT: 'desktop',
      TELEGRAM_BOT_TOKEN: 'inherited-token',
      OPENAI_API_KEY: 'paid-key',
      ANTHROPIC_API_KEY: 'other-paid-key',
      REDIS_NAMESPACE: 'shared:',
      POSTGRES_PASSWORD: 'shared-password',
      COMPOSE_PROFILES: 'tools',
    },
    { project: 'taskbot-e2e-test', port: 18080, token: 'test-token' },
  );
  assert.equal(environment.TELEGRAM_BOT_TOKEN, 'test-token');
  assert.equal(environment.OPENAI_API_KEY, '');
  assert.equal(environment.ANTHROPIC_API_KEY, '');
  assert.equal(environment.TASK_EXTRACTOR, 'heuristic');
  assert.equal(environment.TRANSCRIPTION_PROVIDER, 'openai');
  assert.equal(environment.REDIS_NAMESPACE, undefined);
  assert.equal(environment.POSTGRES_PASSWORD, 'AcceptancePassword');
  assert.equal(environment.WEB_PORT, '18080');
  assert.equal(environment.DOCKER_CONTEXT, 'desktop');
  assert.equal(environment.COMPOSE_PROFILES, undefined);
});

test('destructive cleanup refuses shared and delivered project names', () => {
  for (const name of [
    'taskbot',
    'taskbot-app',
    'taskbot-e2e',
    'other-e2e-test',
    'taskbot-e2e-../x',
  ]) {
    assert.throws(() => assertOwnedProject(name), /owned acceptance project/);
  }
  assert.doesNotThrow(() => assertOwnedProject('taskbot-e2e-123-a1b2'));
});

test('cleanup runs once after failure and after an interrupt', async () => {
  let cleaned = 0;
  await assert.rejects(
    withCleanup(
      async () => {
        throw new Error('failed');
      },
      async () => {
        cleaned++;
      },
    ),
    /failed/,
  );
  assert.equal(cleaned, 1);
  const source = new EventEmitter();
  await assert.rejects(
    withCleanup(
      async (signal) => {
        const result = runCommand(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
          signal,
          timeout: 10000,
        });
        source.emit('SIGINT');
        await result;
      },
      async () => {
        cleaned++;
      },
      source,
    ),
    /interrupt/i,
  );
  assert.equal(cleaned, 2);
  assert.equal(source.listenerCount('SIGINT'), 0);
});

test('commands bound stalled children and preserve binary dumps without exposing output', async () => {
  const started = Date.now();
  await assert.rejects(
    runCommand(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { timeout: 100 }),
    /timed out/,
  );
  // The launcher exits while its descendant still owns the captured output pipe.
  await assert.rejects(
    runCommand(
      process.execPath,
      [
        '-e',
        `require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio: 'inherit'}).unref()`,
      ],
      { timeout: 100 },
    ),
    /timed out/,
  );
  assert.ok(Date.now() - started < 5000);
  const bytes = Buffer.from([0, 1, 128, 255]);
  const result = await runCommand(process.execPath, ['-e', 'process.stdin.pipe(process.stdout)'], {
    input: bytes,
    encoding: 'buffer',
  });
  assert.deepEqual(result.stdout, bytes);
  await assert.rejects(
    runCommand(process.execPath, ['-e', 'console.error("secret-value");process.exit(7)']),
    (error) => error.message.includes('7') && !error.message.includes('secret-value'),
  );
});
