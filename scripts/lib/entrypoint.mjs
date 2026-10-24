import { existsSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const isMain = (url) =>
  process.argv[1] !== undefined &&
  existsSync(process.argv[1]) &&
  realpathSync(process.argv[1]) === realpathSync(fileURLToPath(url));
