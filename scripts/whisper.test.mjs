import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import {
  chmodSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import test from 'node:test';

import { REPOSITORY_ROOT } from './lib/env.mjs';

const waitForFile = async (path) => {
  const deadline = Date.now() + 30000;
  while (!existsSync(path) && Date.now() < deadline) await setTimeout(20);
  assert.ok(existsSync(path), 'the fake server must produce its readiness marker');
};

const stopGroup = (pid) => {
  if (!pid) return;
  try {
    process.kill(-pid, 'SIGKILL');
  } catch (error) {
    if (error.code !== 'ESRCH') throw error;
  }
};

const whisperFixture = (t, body = 'process.exit(3);') => {
  const root = mkdtempSync(join(tmpdir(), 'taskbot-whisper-test-'));
  const bin = join(root, 'bin');
  const marker = join(root, 'server');
  let launcher;
  t.after(() => {
    stopGroup(launcher?.pid);
    if (existsSync(marker + '.pid')) stopGroup(Number(readFileSync(marker + '.pid', 'utf8')));
    rmSync(root, { recursive: true, force: true });
  });
  cpSync(join(REPOSITORY_ROOT, 'scripts'), join(root, 'scripts'), { recursive: true });
  mkdirSync(bin);
  writeFileSync(join(root, 'model.bin'), 'synthetic model');
  writeFileSync(join(root, '.env'), `WHISPER_DEV_MODEL_PATH=${join(root, 'model.bin')}\n`);
  for (const [name, source] of [
    ['ffmpeg', '#!/bin/sh\nexit 0\n'],
    [
      'whisper-server',
      `#!${process.execPath}
const fs = require('node:fs');
const marker = process.env.MARKER;
fs.writeFileSync(marker + '.pid', String(process.pid));
${body}
fs.writeFileSync(marker, JSON.stringify(process.argv.slice(2)));
`,
    ],
  ]) {
    writeFileSync(join(bin, name), source);
    chmodSync(join(bin, name), 0o755);
  }
  const env = { ...process.env, PATH: bin, MARKER: marker };
  delete env.WHISPER_DEV_MODEL_PATH;
  const options = { cwd: root, env, encoding: 'utf8', timeout: 30000 };
  return {
    root,
    marker,
    env,
    run: () => spawnSync(process.execPath, ['scripts/dev-whisper.mjs'], options),
    start: async () => {
      launcher = spawn(process.execPath, ['scripts/dev-whisper.mjs'], {
        cwd: root,
        env,
        detached: true,
        stdio: 'ignore',
      });
      const exited = new Promise((resolveExit) => {
        launcher.once('exit', (code, signal) => resolveExit({ code, signal }));
      });
      await waitForFile(marker);
      return { launcher, exited };
    },
  };
};

test('whisper preflight names each missing input or executable', async (t) => {
  const cases = [
    ['missing .env', (root) => rmSync(join(root, '.env')), /\.env not found/],
    ['missing variable', (root) => writeFileSync(join(root, '.env'), ''), /WHISPER_DEV_MODEL_PATH/],
    [
      'placeholder model',
      (root) =>
        writeFileSync(join(root, '.env'), 'WHISPER_DEV_MODEL_PATH=/absolute/path/model.bin\n'),
      /WHISPER_DEV_MODEL_PATH/,
    ],
    ['missing model', (root) => rmSync(join(root, 'model.bin')), /WHISPER_DEV_MODEL_PATH/],
    [
      'model directory',
      (root) => writeFileSync(join(root, '.env'), `WHISPER_DEV_MODEL_PATH=${root}\n`),
      /WHISPER_DEV_MODEL_PATH/,
    ],
    [
      'missing whisper-server',
      (root) => rmSync(join(root, 'bin/whisper-server')),
      /whisper-server.*PATH/,
    ],
    ['missing ffmpeg', (root) => rmSync(join(root, 'bin/ffmpeg')), /ffmpeg.*PATH/],
  ];
  for (const [name, prepare, message] of cases) {
    await t.test(name, (t) => {
      const { root, marker, run } = whisperFixture(t);
      prepare(root);
      const result = run();
      assert.equal(result.status, 1, result.stderr);
      assert.match(result.stderr, message);
      assert.equal(existsSync(marker + '.pid'), false, 'preflight must not launch the server');
    });
  }
});

test('whisper expands ~/ and preserves a server failure exit code', (t) => {
  const modelDirectory = mkdtempSync(join(homedir(), '.taskbot-whisper-model-test-'));
  t.after(() => rmSync(modelDirectory, { recursive: true, force: true }));
  const model = join(modelDirectory, 'model.bin');
  writeFileSync(model, 'synthetic model');
  const { env, marker, run } = whisperFixture(
    t,
    'fs.writeFileSync(marker, JSON.stringify(process.argv.slice(2))); process.exit(3);',
  );
  env.WHISPER_DEV_MODEL_PATH = `~/${relative(homedir(), model)}`;
  const result = run();
  assert.equal(result.status, 3, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(marker, 'utf8')).slice(0, 2), ['-m', model]);
});

test('whisper waits for a slow server readiness marker', { timeout: 35000 }, async (t) => {
  const { start } = whisperFixture(
    t,
    'Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2500); setInterval(() => {}, 1000);',
  );
  const { launcher, exited } = await start();
  launcher.kill('SIGTERM');
  assert.deepEqual(await exited, { code: 0, signal: null });
});

for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  test(`whisper exits successfully after forwarding ${signal}`, { timeout: 35000 }, async (t) => {
    const { start } = whisperFixture(t, 'setInterval(() => {}, 1000);');
    const { launcher, exited } = await start();
    launcher.kill(signal);
    assert.deepEqual(await exited, { code: 0, signal: null });
  });
}

test(
  'a second interrupt kills a server that ignores its first signal',
  { timeout: 65000 },
  async (t) => {
    const { start, marker } = whisperFixture(
      t,
      "process.on('SIGINT', () => fs.appendFileSync(marker + '.signals', 'SIGINT\\n')); setInterval(() => {}, 1000);",
    );
    const { launcher, exited } = await start();
    launcher.kill('SIGINT');
    await waitForFile(marker + '.signals');
    assert.equal(readFileSync(marker + '.signals', 'utf8'), 'SIGINT\n');
    launcher.kill('SIGINT');
    assert.deepEqual(await exited, { code: 0, signal: null });
    assert.equal(readFileSync(marker + '.signals', 'utf8'), 'SIGINT\n');
  },
);
