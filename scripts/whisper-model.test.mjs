import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { REPOSITORY_ROOT } from './lib/env.mjs';
import { runCommand } from './lib/stack-runtime.mjs';

const modelFixture = (t) => {
  const root = mkdtempSync(join(tmpdir(), 'taskbot-model-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const bin = join(root, 'bin');
  const models = join(root, 'models');
  mkdirSync(bin);
  mkdirSync(models);
  writeFileSync(
    join(bin, 'curl'),
    '#!/bin/sh\nwhile [ "$1" != "--output" ]; do shift; done\nprintf broken > "$2"\n',
  );
  chmodSync(join(bin, 'curl'), 0o755);
  if (spawnSync('sha256sum', ['--version']).error?.code === 'ENOENT') {
    writeFileSync(join(bin, 'sha256sum'), '#!/bin/sh\nexec shasum -a 256 "$@"\n');
    chmodSync(join(bin, 'sha256sum'), 0o755);
  }
  return {
    bin,
    models,
    run: (model) =>
      spawnSync('sh', [join(REPOSITORY_ROOT, 'docker/download-whisper-model.sh'), model, models], {
        encoding: 'utf8',
        timeout: 5000,
        env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
      }),
  };
};

test('a stalled model transfer exits nonzero and leaves no final model', async (t) => {
  const { bin, models } = modelFixture(t);
  const server = createServer((_request, response) => {
    response.writeHead(200, { 'content-length': '100000' });
    response.write('x');
  });
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const realCurl = spawnSync('which', ['curl'], { encoding: 'utf8' }).stdout.trim();
  const marker = join(bin, 'curl-arguments.json');
  // Keep the production options, shorten their values and route only this test to localhost.
  writeFileSync(
    join(bin, 'curl'),
    `#!${process.execPath}\nconst fs=require('node:fs');const args=process.argv.slice(2);fs.writeFileSync(${JSON.stringify(marker)},JSON.stringify(args));for(let i=0;i<args.length;i++){if(['--connect-timeout','--max-time','--retry-max-time','--speed-time'].includes(args[i])) args[i+1]='1';if(args[i]==='--retry') args[i+1]='0';if(args[i].startsWith('https://')) args[i]='http://127.0.0.1:${server.address().port}/model';}const r=require('node:child_process').spawnSync(${JSON.stringify(realCurl)},args,{stdio:'inherit'});process.exit(r.status??1);\n`,
  );
  const started = Date.now();
  const result = await runCommand(
    'sh',
    [join(REPOSITORY_ROOT, 'docker/download-whisper-model.sh'), 'base', models],
    {
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
      timeout: 5000,
      allowFailure: true,
    },
  );
  assert.ok(result.code > 0);
  assert.ok(Date.now() - started < 4500);
  assert.equal(existsSync(join(models, 'ggml-base.bin')), false);
  const args = JSON.parse(readFileSync(marker, 'utf8'));
  for (const flag of [
    '--connect-timeout',
    '--max-time',
    '--speed-limit',
    '--speed-time',
    '--retry-max-time',
    '--retry-all-errors',
  ])
    assert.ok(args.includes(flag), flag);
});

test('an unknown whisper model names the setting and creates no model', (t) => {
  const { models, run } = modelFixture(t);
  const result = run('unknown');
  assert.equal(result.status, 2);
  assert.match(result.stderr, /WHISPER_MODEL/);
  assert.equal(existsSync(join(models, 'ggml-unknown.bin')), false);
});

test('a corrupt model download never becomes the final model', (t) => {
  const { models, run } = modelFixture(t);
  const result = run('base');
  assert.notEqual(result.status, 0);
  assert.equal(existsSync(join(models, 'ggml-base.bin')), false);
  assert.equal(readFileSync(join(models, 'ggml-base.bin.part'), 'utf8'), 'broken');
});

test('an existing verified model is reused without a download', (t) => {
  const { models, run } = modelFixture(t);
  writeFileSync(join(models, 'ggml-base.bin'), 'verified fixture');
  const result = run('base');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(join(models, 'ggml-base.bin'), 'utf8'), 'verified fixture');
  assert.equal(existsSync(join(models, 'ggml-base.bin.part')), false);
});
