import { spawnSync } from 'node:child_process';
import { join } from 'node:path';
import { isMain } from './lib/entrypoint.mjs';
import { loadEnv, REPOSITORY_ROOT } from './lib/env.mjs';

export const buildInfraCommand = (action, environment = process.env, root = REPOSITORY_ROOT) => {
  if (!['up', 'stop'].includes(action)) throw new Error('Usage: infra.mjs up|stop');
  const stack = environment.TASKBOT_STACK || 'taskbot';
  if (!/^taskbot(?:-[a-z0-9][a-z0-9_-]*)?$/.test(stack)) {
    throw new Error(
      'An isolated stack name must start with taskbot- and use lowercase letters or digits.',
    );
  }
  const pg = environment.TASKBOT_PG_PORT || '5432';
  const redis = environment.TASKBOT_REDIS_PORT || '6379';
  for (const port of [pg, redis]) {
    if (!/^[1-9]\d{0,4}$/.test(port) || Number(port) > 65535)
      throw new Error('Stack ports must be integers from 1 to 65535.');
  }
  if (stack === 'taskbot' && (pg !== '5432' || redis !== '6379')) {
    throw new Error(
      'The shared stack taskbot uses ports 5432 and 6379. For other ports set TASKBOT_STACK=taskbot-<name> (its own containers and volumes).',
    );
  }
  if (
    stack !== 'taskbot' &&
    ([pg, redis].some((port) => ['5432', '6379'].includes(port)) || pg === redis)
  ) {
    throw new Error('An isolated stack needs its own distinct PostgreSQL and Redis ports.');
  }
  const env = {
    ...environment,
    TASKBOT_STACK: stack,
    TASKBOT_PG_PORT: pg,
    TASKBOT_REDIS_PORT: redis,
  };
  if (stack === 'taskbot') {
    for (const key of ['POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB']) delete env[key];
  }
  return {
    args: [
      'compose',
      '--project-name',
      stack,
      '--file',
      join(root, 'docker-compose.yml'),
      '--file',
      join(root, 'docker/dev-infra.ports.yml'),
      '--project-directory',
      root,
      ...(stack === 'taskbot' ? ['--env-file', join(root, 'scripts/empty.env')] : []),
      ...(action === 'up' ? ['up', '-d', '--wait', 'postgres', 'redis'] : ['stop']),
    ],
    env,
  };
};

if (isMain(import.meta.url)) {
  try {
    loadEnv(REPOSITORY_ROOT, false);
    const { args, env } = buildInfraCommand(process.argv[2]);
    const result = spawnSync('docker', args, { env, cwd: REPOSITORY_ROOT, stdio: 'inherit' });
    if (result.error) throw result.error;
    process.exitCode = result.status ?? 1;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
