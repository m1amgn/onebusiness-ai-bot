import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import test from 'node:test';

import { checkApiSchema } from './check-api-schema.mjs';
import { findOversizedFiles } from './check-file-size.mjs';
import { buildInfraCommand } from './infra.mjs';
import { loadEnv, REPOSITORY_ROOT } from './lib/env.mjs';

const fixture = (t) => {
  const directory = mkdtempSync(join(tmpdir(), 'taskbot-tooling-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
};
const write = (root, name, content) => {
  const path = join(root, name);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content);
  return path;
};
const stopGroup = (pid) => {
  if (!pid) return;
  try {
    process.kill(-pid, 'SIGKILL');
  } catch {}
};

test('size check detects 701 lines, accepts 700 and exempts tests and generated files', (t) => {
  const root = fixture(t);
  assert.equal(spawnSync('git', ['init', '-q', root]).status, 0);
  write(root, 'fine.py', 'x\n'.repeat(700));
  write(root, 'large.ts', 'x\n'.repeat(700) + 'last line');
  for (const name of ['large.mjs', 'large.css', 'large.tsx', 'x.test.d/big.py']) {
    write(root, name, 'x\n'.repeat(701));
  }
  symlinkSync('large.ts', join(root, 'linked.py'));
  write(root, 'tests/large.py', 'x\n'.repeat(900));
  write(root, 'frontend/src/lib/large.test.ts', 'x\n'.repeat(900));
  write(root, 'frontend/src/api/generated/large.ts', 'x\n'.repeat(900));
  assert.deepEqual(findOversizedFiles(root), [
    { path: 'large.css', lines: 701 },
    { path: 'large.mjs', lines: 701 },
    { path: 'large.ts', lines: 701 },
    { path: 'large.tsx', lines: 701 },
    { path: 'x.test.d/big.py', lines: 701 },
  ]);
  for (const name of ['large.ts', 'large.mjs', 'large.css', 'large.tsx', 'x.test.d/big.py']) {
    rmSync(join(root, name));
  }
  assert.deepEqual(findOversizedFiles(root), []);
});

test('missing required environment file has an actionable one-line error', (t) => {
  assert.throws(() => loadEnv(fixture(t)), /^Error: \.env not found: copy \.env.example to \.env$/);
});

test('infrastructure permits defaults and pins the validated isolated target', () => {
  const shared = buildInfraCommand('up', {}, '/repository');
  assert.deepEqual(shared.args, [
    'compose',
    '--project-name',
    'taskbot',
    '--file',
    '/repository/docker-compose.yml',
    '--project-directory',
    '/repository',
    'up',
    '-d',
    '--wait',
    'postgres',
    'redis',
  ]);
  const env = {
    TASKBOT_STACK: 'taskbot-test',
    TASKBOT_PG_PORT: '15432',
    TASKBOT_REDIS_PORT: '16379',
    COMPOSE_PROJECT_NAME: 'taskbot',
    COMPOSE_FILE: 'other.yml',
  };
  for (const action of ['up', 'stop']) {
    const command = buildInfraCommand(action, env, '/repository');
    assert.deepEqual(command.args.slice(0, 7), [
      'compose',
      '--project-name',
      'taskbot-test',
      '--file',
      '/repository/docker-compose.yml',
      '--project-directory',
      '/repository',
    ]);
    assert.equal(command.args[7], action);
  }
});

test('infrastructure refuses unsafe names, ports and incomplete isolated settings', () => {
  for (const env of [
    { TASKBOT_PG_PORT: '5433' },
    { TASKBOT_REDIS_PORT: '16379' },
    { TASKBOT_STACK: 'taskbot-x' },
    { TASKBOT_STACK: 'other' },
    { TASKBOT_STACK: 'taskbot-x', TASKBOT_PG_PORT: '15432', TASKBOT_REDIS_PORT: '5432' },
    { TASKBOT_PG_PORT: 'invalid' },
    { TASKBOT_PG_PORT: '65536' },
    { TASKBOT_STACK: 'taskbot-x', TASKBOT_PG_PORT: '15432', TASKBOT_REDIS_PORT: '15432' },
  ])
    assert.throws(() => buildInfraCommand('up', env, '/repository'));
});

test('infrastructure rejects invalid names before valid isolated ports can reach Docker', () => {
  for (const stack of ['other', 'taskbot-', 'taskbot-X']) {
    assert.throws(
      () =>
        buildInfraCommand('up', {
          TASKBOT_STACK: stack,
          TASKBOT_PG_PORT: '15432',
          TASKBOT_REDIS_PORT: '16379',
        }),
      /An isolated stack name must start with taskbot-/,
    );
  }
});

test('infrastructure rejects port syntax and range independently of isolation rules', () => {
  for (const key of ['TASKBOT_PG_PORT', 'TASKBOT_REDIS_PORT']) {
    for (const port of ['0', '65536', '05432', 'invalid']) {
      assert.throws(
        () =>
          buildInfraCommand('up', {
            TASKBOT_STACK: 'taskbot-x',
            TASKBOT_PG_PORT: '15432',
            TASKBOT_REDIS_PORT: '16379',
            [key]: port,
          }),
        /Stack ports must be integers from 1 to 65535/,
      );
    }
  }
});

test('direct scripts run when import.meta.main is unavailable', (t) => {
  const root = fixture(t);
  cpSync(join(REPOSITORY_ROOT, 'scripts'), join(root, 'scripts'), { recursive: true });
  assert.equal(spawnSync('git', ['init', '-q', root]).status, 0);
  write(root, 'large.py', 'x\n'.repeat(701));
  for (const [name, message] of [
    ['check-file-size.mjs', /large\.py: 701 lines/],
    ['check-api-schema.mjs', /failed|No such file/],
    ['infra.mjs', /Usage: infra\.mjs/],
    ['dev-whisper.mjs', /\.env not found/],
  ]) {
    const path = join(root, 'scripts', name);
    writeFileSync(path, 'import.meta.main = undefined;\n' + readFileSync(path, 'utf8'));
    const result = spawnSync(process.execPath, [path], { cwd: root, encoding: 'utf8' });
    assert.equal(result.status, 1, name);
    assert.match(result.stderr, message, name);
  }
});

test('future-stage command fails with its building stage', () => {
  const result = spawnSync(process.execPath, ['scripts/not-built.mjs', 'dev:bot', 'S4'], {
    cwd: REPOSITORY_ROOT,
    encoding: 'utf8',
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /dev:bot.*built in stage S4/);
});

test('schema check runs the real exporter/generator and detects both kinds of stale output', (t) => {
  const root = fixture(t);
  write(root, 'package.json', readFileSync(join(REPOSITORY_ROOT, 'package.json')));
  mkdirSync(join(root, 'backend'));
  copyFileSync(join(REPOSITORY_ROOT, 'backend/openapi.json'), join(root, 'backend/openapi.json'));
  for (const name of [
    'package.json',
    'openapi-ts.config.ts',
    '.prettierrc.json',
    '.prettierignore',
    'src/index.css',
  ]) {
    write(root, `frontend/${name}`, readFileSync(join(REPOSITORY_ROOT, 'frontend', name)));
  }
  cpSync(
    join(REPOSITORY_ROOT, 'frontend/src/api/generated'),
    join(root, 'frontend/src/api/generated'),
    { recursive: true },
  );
  symlinkSync(join(REPOSITORY_ROOT, 'node_modules'), join(root, 'node_modules'), 'dir');
  const backend = join(REPOSITORY_ROOT, 'backend');
  assert.deepEqual(checkApiSchema(root, backend), []);
  write(root, 'backend/openapi.json', '{}\n');
  assert.deepEqual(checkApiSchema(root, backend), ['backend/openapi.json']);
  copyFileSync(join(REPOSITORY_ROOT, 'backend/openapi.json'), join(root, 'backend/openapi.json'));
  write(root, 'frontend/src/api/generated/zod.gen.ts', '// stale\n');
  assert.ok(
    checkApiSchema(root, backend).some((path) => path.startsWith('frontend/src/api/generated')),
  );
  rmSync(join(root, 'frontend/src/api/generated/zod.gen.ts'));
  assert.deepEqual(checkApiSchema(root, backend), ['frontend/src/api/generated/zod.gen.ts']);
  copyFileSync(
    join(REPOSITORY_ROOT, 'frontend/src/api/generated/zod.gen.ts'),
    join(root, 'frontend/src/api/generated/zod.gen.ts'),
  );
  write(root, 'frontend/src/api/generated/extra.ts', '// stale\n');
  assert.deepEqual(checkApiSchema(root, backend), ['frontend/src/api/generated/extra.ts']);
  const manifest = JSON.parse(readFileSync(join(root, 'frontend/package.json'), 'utf8'));
  manifest.scripts['api:generate'] = 'node -e "process.exit(0)"';
  write(root, 'frontend/package.json', JSON.stringify(manifest));
  assert.throws(() => checkApiSchema(root, backend), /API generator wrote no files/);
  assert.equal(existsSync(join(root, 'frontend/.api-check')), false);
});

test(
  'whisper launcher forwards SIGTERM and passes conversion/auto-language arguments',
  { timeout: 10000 },
  async (t) => {
    const root = fixture(t);
    cpSync(join(REPOSITORY_ROOT, 'scripts'), join(root, 'scripts'), { recursive: true });
    write(root, '.env', `WHISPER_DEV_MODEL_PATH=${join(root, 'model.bin')}\n`);
    write(root, 'model.bin', 'synthetic model placeholder');
    const bin = join(root, 'bin');
    const marker = join(root, 'signal.json');
    const executable = (name, body) => {
      const path = write(root, `bin/${name}`, body);
      return spawnSync('chmod', ['+x', path]).status;
    };
    assert.equal(executable('ffmpeg', '#!/bin/sh\nexit 0\n'), 0);
    assert.equal(
      executable(
        'whisper-server',
        `#!${process.execPath}\nconst fs=require('node:fs');fs.writeFileSync(process.env.MARKER+'.pid',String(process.pid));fs.writeFileSync(process.env.MARKER,JSON.stringify(process.argv.slice(2)));process.on('SIGTERM',()=>{fs.writeFileSync(process.env.MARKER,'SIGTERM');process.exit(0)});setInterval(()=>{},1000);\n`,
      ),
      0,
    );
    const child = spawn(process.execPath, ['scripts/dev-whisper.mjs'], {
      cwd: root,
      detached: true,
      stdio: 'ignore',
      env: {
        ...process.env,
        PATH: bin,
        MARKER: marker,
        WHISPER_DEV_MODEL_PATH: join(root, 'model.bin'),
      },
    });
    let serverPid;
    t.after(() => {
      stopGroup(child.pid);
      stopGroup(serverPid);
    });
    const exited = new Promise((resolveExit) => child.once('exit', resolveExit));
    for (let attempt = 0; attempt < 100 && !existsSync(marker); attempt++) await setTimeout(20);
    if (existsSync(marker + '.pid')) serverPid = Number(readFileSync(marker + '.pid', 'utf8'));
    assert.ok(existsSync(marker), 'the child server must start');
    assert.deepEqual(JSON.parse(readFileSync(marker, 'utf8')), [
      '-m',
      join(root, 'model.bin'),
      '--host',
      '127.0.0.1',
      '--port',
      '8178',
      '--convert',
      '-l',
      'auto',
    ]);
    child.kill('SIGTERM');
    await exited;
    assert.equal(readFileSync(marker, 'utf8'), 'SIGTERM');
  },
);

test('terminal SIGINT reaches the server only once', { timeout: 10000 }, async (t) => {
  const root = fixture(t);
  cpSync(join(REPOSITORY_ROOT, 'scripts'), join(root, 'scripts'), { recursive: true });
  write(root, '.env', `WHISPER_DEV_MODEL_PATH=${join(root, 'model.bin')}\n`);
  write(root, 'model.bin', 'synthetic');
  const marker = join(root, 'signals');
  for (const [name, body] of [
    ['ffmpeg', '#!/bin/sh\nexit 0\n'],
    [
      'whisper-server',
      `#!${process.execPath}\nconst fs=require('node:fs');fs.writeFileSync(process.env.MARKER+'.pid',String(process.pid));fs.writeFileSync(process.env.MARKER,'');process.on('SIGINT',()=>{fs.appendFileSync(process.env.MARKER,'SIGINT\\n');setTimeout(()=>process.exit(0),200)});setInterval(()=>{},1000);\n`,
    ],
  ]) {
    const path = write(root, `bin/${name}`, body);
    assert.equal(spawnSync('chmod', ['+x', path]).status, 0);
  }
  const child = spawn(process.execPath, ['scripts/dev-whisper.mjs'], {
    cwd: root,
    detached: true,
    stdio: 'ignore',
    env: {
      ...process.env,
      PATH: join(root, 'bin'),
      MARKER: marker,
      WHISPER_DEV_MODEL_PATH: join(root, 'model.bin'),
    },
  });
  let serverPid;
  t.after(() => {
    stopGroup(child.pid);
    stopGroup(serverPid);
  });
  const exited = new Promise((resolveExit) => child.once('exit', resolveExit));
  for (let attempt = 0; attempt < 100 && !existsSync(marker); attempt++) await setTimeout(20);
  if (existsSync(marker + '.pid')) serverPid = Number(readFileSync(marker + '.pid', 'utf8'));
  assert.ok(existsSync(marker), 'the child server must start');
  process.kill(-child.pid, 'SIGINT');
  await exited;
  assert.equal(readFileSync(marker, 'utf8'), 'SIGINT\n');
});
