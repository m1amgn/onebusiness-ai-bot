import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';

const actions = new Set(['up', 'down', 'run', 'exec', 'logs', 'ps', 'stop', 'restart', 'build']);
const flags = new Set([
  '--build',
  '-d',
  '--detach',
  '--wait',
  '--rm',
  '-v',
  '--volumes',
  '--remove-orphans',
  '--no-build',
  '--no-deps',
  '--follow',
  '-f',
]);
const valuedFlags = new Set(['--wait-timeout', '--tail', '--timeout', '-t']);

const checkCompose = (words, services) => {
  const profiles = new Set(Object.values(services).flatMap((service) => service.profiles ?? []));
  let index = 2;
  while (words[index] === '--profile') {
    assert.ok(
      profiles.has(words[index + 1]),
      `Unknown README Compose profile: ${words[index + 1]}`,
    );
    index += 2;
  }
  const action = words[index++];
  assert.ok(actions.has(action), `Unsupported README Compose command: ${action}`);
  const selected = [];
  for (; index < words.length; index++) {
    const word = words[index];
    if (valuedFlags.has(word)) {
      assert.ok(words[++index], `Missing README Compose option value: ${word}`);
    } else if (word.startsWith('-')) {
      assert.ok(flags.has(word), `Unsupported README Compose option: ${word}`);
    } else {
      assert.ok(Object.hasOwn(services, word), `Unknown README Compose service: ${word}`);
      selected.push(word);
      if (['run', 'exec'].includes(action)) break; // Remaining arguments belong to the service.
    }
  }
  if (['run', 'exec'].includes(action))
    assert.equal(selected.length, 1, 'README command needs a service');
  if (action === 'down') assert.equal(selected.length, 0, 'Compose down accepts no services');
};

export const checkReadmeCommands = async (root, services) => {
  const readme = await readFile(join(root, 'README.md'), 'utf8');
  const manifest = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
  const candidates = [];
  for (const block of readme.matchAll(/^[ \t]*```sh\s*\n([\s\S]*?)^[ \t]*```/gm)) {
    for (const raw of block[1].replace(/\\\n\s*/g, ' ').split('\n')) {
      const line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      candidates.push(line);
    }
  }
  for (const inline of readme.matchAll(/`((?:docker compose|yarn|node scripts\/)\s[^`\n]+)`/g))
    candidates.push(inline[1]);
  const commands = [...new Set(candidates)];
  for (const line of commands) {
    const words = (line.match(/"[^"\n]*"|'[^'\n]*'|[^\s]+/g) ?? []).map((word) =>
      word.replace(/^(['"])(.*)\1$/, '$2'),
    );
    const [command] = words;
    if (command === 'docker' && words[1] === 'compose') checkCompose(words, services);
    else if (command === 'yarn') {
      const script = words[1] === 'run' ? words[2] : words[1];
      assert.ok(Object.hasOwn(manifest.scripts, script), `Unknown README yarn script: ${script}`);
    } else if (command === 'node') {
      assert.match(
        words[1] ?? '',
        /^scripts\/[\w/-]+\.mjs$/,
        'README node command must name a script',
      );
      assert.ok((await stat(join(root, words[1]))).isFile(), 'README node script must exist');
    } else {
      assert.deepEqual(
        words,
        ['cp', '.env.example', '.env'],
        `Unsupported README shell command: ${command}`,
      );
      assert.ok((await stat(join(root, '.env.example'))).isFile());
    }
  }
  assert.ok(commands.length, 'README has no shell commands to verify');
  return commands;
};
