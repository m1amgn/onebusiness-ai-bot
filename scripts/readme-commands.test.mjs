import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { checkReadme, checkReadmeCommands } from './lib/readme-commands.mjs';
import { acceptanceEnvironment, runCommand } from './lib/stack-runtime.mjs';
import { REPOSITORY_ROOT } from './lib/env.mjs';

test('every README shell command names an existing service, profile, package script or file', async () => {
  const commands = await checkReadme({
    root: REPOSITORY_ROOT,
    compose: (args) =>
      runCommand(
        'docker',
        [
          'compose',
          '--project-name',
          'taskbot-e2e-readme-check',
          '--project-directory',
          REPOSITORY_ROOT,
          '--file',
          join(REPOSITORY_ROOT, 'docker-compose.yml'),
          '--env-file',
          join(REPOSITORY_ROOT, 'scripts/empty.env'),
          ...args,
        ],
        { env: acceptanceEnvironment(process.env, { project: 'taskbot-e2e-readme-check' }) },
      ),
  });
  assert.ok(commands.includes('node scripts/stack-acceptance.mjs'));
  assert.ok(commands.includes('yarn verify'));
});

test('stale README commands fail validation instead of validating their own echo', async (t) => {
  const root = await mkdtemp(join(tmpdir(), 'taskbot-readme-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'scripts'));
  await writeFile(join(root, 'scripts/check.mjs'), '');
  await writeFile(join(root, '.env.example'), '');
  await writeFile(
    join(root, 'package.json'),
    JSON.stringify({ scripts: { verify: 'node scripts/check.mjs' } }),
  );
  const services = { api: {}, seed: { profiles: ['tools'] } };
  const check = async (command) => {
    await writeFile(join(root, 'README.md'), `   \x60\x60\x60sh\n   ${command}\n   \x60\x60\x60\n`);
    return checkReadmeCommands(root, services);
  };
  for (const command of [
    'cp .env.example .env',
    'docker compose up --build -d --wait api',
    'docker compose run --rm seed 1 "Synthetic text"',
    'docker compose --profile tools up seed',
    'yarn verify',
    'node scripts/check.mjs',
  ])
    await assert.doesNotReject(check(command));
  for (const command of [
    'docker compose up absent',
    'docker compose --profile unknown up api',
    'docker compose run --rm absent',
    'yarn missing',
    'node scripts/missing.mjs',
  ])
    await assert.rejects(check(command));
  await writeFile(join(root, 'README.md'), '`docker compose up absent`');
  await assert.rejects(checkReadmeCommands(root, services), /Unknown README Compose service/);
});
