import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { isMain } from './lib/entrypoint.mjs';
import { REPOSITORY_ROOT } from './lib/env.mjs';

const run = (command, args, options) => {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} failed:\n${result.stdout}${result.stderr}`);
};
const files = (directory, prefix = '') => {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const name = prefix ? `${prefix}/${entry.name}` : entry.name;
      return entry.isDirectory() ? files(join(directory, entry.name), name) : [name];
    })
    .sort();
};

export const checkApiSchema = (root = REPOSITORY_ROOT, backend = join(root, 'backend')) => {
  const temporary = mkdtempSync(join(tmpdir(), 'taskbot-schema-'));
  const schema = join(temporary, 'openapi.json');
  const output = join(root, 'frontend/.api-check');
  const generated = join(root, 'frontend/src/api/generated');
  try {
    run(
      'uv',
      [
        'run',
        '--locked',
        '--directory',
        backend,
        'python',
        '-m',
        'taskbot.entrypoints.api.export_schema',
        schema,
      ],
      { cwd: root },
    );
    if (
      !existsSync(join(root, 'backend/openapi.json')) ||
      !readFileSync(schema).equals(readFileSync(join(root, 'backend/openapi.json')))
    ) {
      return ['backend/openapi.json'];
    }
    rmSync(output, { recursive: true, force: true });
    run('yarn', ['--cwd', join(root, 'frontend'), 'api:generate'], {
      cwd: root,
      env: { ...process.env, SCHEMA_IN: schema, SCHEMA_OUT: output },
    });
    const actual = files(output);
    if (actual.length === 0) throw new Error('API generator wrote no files in frontend/.api-check');
    // The scratch directory is ignored by ordinary formatting checks.
    run(
      'yarn',
      [
        '--cwd',
        join(root, 'frontend'),
        'prettier',
        '--ignore-path',
        '/dev/null',
        '--config',
        join(root, 'frontend/.prettierrc.json'),
        '--write',
        output,
      ],
      { cwd: root },
    );
    const committed = files(generated);
    return [...new Set([...actual, ...committed])]
      .sort()
      .filter(
        (name) =>
          !actual.includes(name) ||
          !committed.includes(name) ||
          !readFileSync(join(output, name)).equals(readFileSync(join(generated, name))),
      )
      .map((name) => `frontend/src/api/generated/${name}`);
  } finally {
    rmSync(output, { recursive: true, force: true });
    rmSync(temporary, { recursive: true, force: true });
  }
};

if (isMain(import.meta.url)) {
  try {
    const stale = checkApiSchema();
    for (const path of stale) console.error(`${path} is stale; run yarn api:schema`);
    process.exitCode = stale.length ? 1 : 0;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
