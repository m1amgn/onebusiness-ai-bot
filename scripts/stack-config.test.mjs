import assert from 'node:assert/strict';
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { REPOSITORY_ROOT } from './lib/env.mjs';
import { checkImagePins, checkSharedConfig } from './lib/stack-config.mjs';

const HASHES = { postgres: 'a'.repeat(64), redis: 'b'.repeat(64) };
const fixture = async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'taskbot-config-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = join(directory, 'repository');
  const scratch = join(directory, 'scratch');
  await mkdir(scratch);
  for (const name of [
    'docker-compose.yml',
    'docker/dev-infra.ports.yml',
    'scripts/empty.env',
    '.env.example',
    'docker/backend.Dockerfile',
    'docker/web.Dockerfile',
    'docker/whisper.Dockerfile',
  ]) {
    await mkdir(dirname(join(root, name)), { recursive: true });
    await copyFile(join(REPOSITORY_ROOT, name), join(root, name));
  }
  // A directory fails any attempt to read or copy the owner's .env as a file.
  await mkdir(join(root, '.env'));
  return {
    root,
    scratch,
    commands: [],
    env: {
      PATH: '/synthetic/bin',
      HOME: '/synthetic/home',
      DOCKER_CONTEXT: 'synthetic-context',
      POSTGRES_USER: 'AcceptanceUser',
      POSTGRES_PASSWORD: 'AcceptancePassword',
      POSTGRES_DB: 'AcceptanceDatabase',
      COMPOSE_FILE: '/owner/compose.yml',
      COMPOSE_PROFILES: 'whisper',
      COMPOSE_PROJECT_NAME: 'taskbot-e2e-example',
      TASKBOT_STACK: 'taskbot-other',
      OPENAI_API_KEY: 'owner-secret',
    },
    docker: () => assert.fail('Config hashes must use the exact scrubbed environment'),
  };
};

test('shared hashes use the infra file order, empty env file and scrubbed shell for four cases', async (t) => {
  const ctx = await fixture(t);
  const observations = [];
  const runner = async (command, args, options) => {
    assert.equal(command, 'docker');
    const root = args[args.indexOf('--project-directory') + 1];
    assert.notEqual(root, ctx.root);
    assert.equal(dirname(root), ctx.scratch);
    assert.deepEqual(args.slice(0, 11), [
      'compose',
      '--project-name',
      'taskbot',
      '--file',
      join(root, 'docker-compose.yml'),
      '--file',
      join(root, 'docker/dev-infra.ports.yml'),
      '--project-directory',
      root,
      '--env-file',
      join(root, 'scripts/empty.env'),
    ]);
    assert.deepEqual(args.slice(11, -1), ['config', '--hash']);
    assert.equal(options.timeout, 30000);
    assert.deepEqual(options.env, {
      PATH: '/synthetic/bin',
      HOME: '/synthetic/home',
      DOCKER_CONTEXT: 'synthetic-context',
      TASKBOT_STACK: 'taskbot',
      TASKBOT_PG_PORT: '5432',
      TASKBOT_REDIS_PORT: '6379',
    });
    for (const name of ['docker-compose.yml', 'docker/dev-infra.ports.yml', 'scripts/empty.env']) {
      assert.equal(
        await readFile(join(root, name), 'utf8'),
        await readFile(join(ctx.root, name), 'utf8'),
      );
    }
    let dotenv;
    try {
      dotenv = await readFile(join(root, '.env'), 'utf8');
    } catch (error) {
      assert.equal(error.code, 'ENOENT');
    }
    observations.push({ root, dotenv, service: args.at(-1) });
    return { stdout: `${args.at(-1)} ${HASHES[args.at(-1)]}\n` };
  };
  const result = await checkSharedConfig(ctx, { runner });
  assert.equal(result.cases, 4);
  assert.deepEqual(Object.keys(result.hashes), ['absent', 'example', 'custom', 'shell']);
  for (const hashes of Object.values(result.hashes)) assert.deepEqual(hashes, HASHES);
  assert.deepEqual(
    observations.map((item) => item.service),
    ['postgres', 'redis', 'postgres', 'redis', 'postgres', 'redis', 'postgres', 'redis'],
  );
  assert.equal(observations[0].dotenv, undefined);
  assert.equal(observations[2].dotenv, await readFile(join(ctx.root, '.env.example'), 'utf8'));
  assert.match(observations[4].dotenv, /^POSTGRES_USER=SyntheticUser$/m);
  assert.match(observations[4].dotenv, /^POSTGRES_PASSWORD=SyntheticPassword$/m);
  assert.match(observations[4].dotenv, /^POSTGRES_DB=SyntheticDatabase$/m);
  assert.equal(observations[6].dotenv, undefined);
  assert.equal(ctx.env.POSTGRES_PASSWORD, 'AcceptancePassword');
  assert.equal(ctx.commands.length, 8);
  assert.ok(ctx.commands.every((label) => !label.includes('secret')));
  assert.deepEqual(await readdir(ctx.scratch), []);
});

test('shared hashes reject a config change and remove synthetic files on failure', async (t) => {
  const ctx = await fixture(t);
  let calls = 0;
  await assert.rejects(
    checkSharedConfig(ctx, {
      runner: async (command, args) => {
        const service = args.at(-1);
        calls++;
        return { stdout: `${service} ${calls === 5 ? 'c'.repeat(64) : HASHES[service]}\n` };
      },
    }),
    /Shared postgres config changed with custom credentials/,
  );
  assert.equal(calls, 5);
  assert.deepEqual(await readdir(ctx.scratch), []);
});

test('shared hashes reject missing service hashes and clean up a failed Docker command', async (t) => {
  const ctx = await fixture(t);
  for (const runner of [
    async () => ({ stdout: `redis ${HASHES.redis}\n` }),
    async () => {
      throw new Error('synthetic Docker failure');
    },
  ]) {
    await assert.rejects(
      checkSharedConfig(ctx, { runner }),
      /hash missing|synthetic Docker failure/,
    );
    assert.deepEqual(await readdir(ctx.scratch), []);
  }
});

const manifest = (architectures) =>
  JSON.stringify({
    schemaVersion: 2,
    manifests: architectures.map((architecture) => ({ platform: { os: 'linux', architecture } })),
  });

test('image pins inspect every distinct base and pulled image with required Linux architectures', async (t) => {
  const ctx = await fixture(t);
  const calls = [];
  ctx.docker = async (args) => {
    assert.deepEqual(args.slice(0, 3), ['buildx', 'imagetools', 'inspect']);
    assert.equal(args.at(-1), '--raw');
    calls.push(args[3]);
    const architectures = args[3].includes('whisper.cpp:main-arm64')
      ? ['arm64']
      : args[3].includes('whisper.cpp:main@')
        ? ['amd64']
        : ['amd64', 'arm64'];
    return { stdout: manifest(architectures) };
  };
  const result = await checkImagePins(ctx);
  assert.equal(calls.length, 8);
  assert.equal(new Set(calls).size, 8);
  assert.deepEqual(result.refs, calls);
  for (const image of ['uv:', 'python:', 'node:', 'nginx-unprivileged:', 'postgres:', 'redis:']) {
    const ref = calls.find((ref) => ref.includes(image));
    assert.ok(ref, `${image} was not inspected`);
    assert.deepEqual(result.platforms[ref], ['linux/amd64', 'linux/arm64']);
  }
});

test('image pins reject missing digests and non-patch tags before registry reads', async (t) => {
  const ctx = await fixture(t);
  const path = join(ctx.root, 'docker/web.Dockerfile');
  const original = await readFile(path, 'utf8');
  for (const changed of [
    original.replace(/node:[^\s]+/, 'node:26.12.1-alpine'),
    original.replace(/node:[^\s]+/, `node:26-alpine@sha256:${'c'.repeat(64)}`),
    original.replace(/node:[^\s]+/, `node:latest@sha256:${'c'.repeat(64)}`),
  ]) {
    await writeFile(path, changed);
    await assert.rejects(checkImagePins(ctx), /literal version tag|exact patch release/);
  }
});

test('image pins validate pulled Compose refs and both whisper architecture stages', async (t) => {
  const ctx = await fixture(t);
  const composePath = join(ctx.root, 'docker-compose.yml');
  const compose = await readFile(composePath, 'utf8');
  await writeFile(composePath, compose.replace(/redis:8[^\s]+/, 'redis:latest'));
  await assert.rejects(checkImagePins(ctx), /literal version tag/);
  await writeFile(composePath, compose);
  const whisperPath = join(ctx.root, 'docker/whisper.Dockerfile');
  const whisper = await readFile(whisperPath, 'utf8');
  await writeFile(whisperPath, whisper.replace(/^FROM .* AS whisper-arm64\n/m, ''));
  await assert.rejects(checkImagePins(ctx), /both pinned stages/);
});

test('image pins reject platform manifests and indexes missing either normal architecture', async (t) => {
  const ctx = await fixture(t);
  for (const stdout of [
    JSON.stringify({ schemaVersion: 2, config: {} }),
    manifest(['amd64']),
    manifest(['arm64']),
  ]) {
    ctx.docker = async () => ({ stdout });
    await assert.rejects(checkImagePins(ctx), /platform index|missing linux\//);
  }
});

test('whisper pins require the matching architecture in each upstream index', async (t) => {
  const ctx = await fixture(t);
  for (const incorrect of ['main@', 'main-arm64@']) {
    ctx.docker = async (args) => ({
      stdout: manifest(
        args[3].includes(`whisper.cpp:${incorrect}`) ? ['unknown'] : ['amd64', 'arm64'],
      ),
    });
    await assert.rejects(checkImagePins(ctx), /Pinned image is missing linux\//);
  }
});
