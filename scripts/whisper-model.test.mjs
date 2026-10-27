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
import { REPOSITORY_ROOT } from './lib/env.mjs';

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
    models,
    run: (model) =>
      spawnSync('sh', [join(REPOSITORY_ROOT, 'docker/download-whisper-model.sh'), model, models], {
        encoding: 'utf8',
        timeout: 5000,
        env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
      }),
  };
};

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
