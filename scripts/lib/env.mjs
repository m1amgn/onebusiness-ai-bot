import { join, resolve } from 'node:path';
export const REPOSITORY_ROOT = resolve(import.meta.dirname, '../..');

export const loadEnv = (root = REPOSITORY_ROOT, required = true) => {
  try {
    process.loadEnvFile(join(root, '.env'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    if (required) throw new Error('.env not found: copy .env.example to .env');
  }
};
