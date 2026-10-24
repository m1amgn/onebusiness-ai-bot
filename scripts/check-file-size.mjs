import { execFileSync } from 'node:child_process';
import { lstatSync, readFileSync } from 'node:fs';
import { basename, extname, join } from 'node:path';
import { isMain } from './lib/entrypoint.mjs';
import { REPOSITORY_ROOT } from './lib/env.mjs';

export const findOversizedFiles = (root = REPOSITORY_ROOT) => {
  const paths = execFileSync(
    'git',
    ['ls-files', '-z', '--cached', '--others', '--exclude-standard'],
    {
      cwd: root,
      encoding: 'utf8',
    },
  )
    .split('\0')
    .filter(Boolean);
  const oversized = [];
  for (const path of paths) {
    if (!['.py', '.ts', '.tsx', '.mjs', '.css'].includes(extname(path))) continue;
    if (/(^|\/)(tests|src\/test|src\/api\/generated|alembic\/versions)\//.test(path)) continue;
    if (/\.test\./.test(basename(path)) || path.includes('/.api-check/')) continue;
    const absolute = join(root, path);
    let stat;
    try {
      stat = lstatSync(absolute);
    } catch (error) {
      if (error.code === 'ENOENT') continue;
      throw error;
    }
    if (!stat.isFile()) continue;
    const content = readFileSync(absolute, 'utf8');
    const lines = content ? content.split('\n').length - Number(content.endsWith('\n')) : 0;
    if (lines > 700) oversized.push({ path, lines });
  }
  return oversized;
};

if (isMain(import.meta.url)) {
  const oversized = findOversizedFiles();
  for (const { path, lines } of oversized) console.error(`${path}: ${lines} lines (maximum 700)`);
  process.exitCode = oversized.length ? 1 : 0;
}
