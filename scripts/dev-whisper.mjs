import { spawn } from 'node:child_process';
import { accessSync, constants, existsSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { delimiter, join } from 'node:path';
import { loadEnv } from './lib/env.mjs';

const requireExecutable = (name) => {
  for (const directory of (process.env.PATH ?? '').split(delimiter)) {
    try {
      accessSync(join(directory, name), constants.X_OK);
      return;
    } catch {}
  }
  throw new Error(`${name} is not on PATH; install it before running yarn dev:whisper`);
};

export const startWhisper = () => {
  loadEnv();
  const configured = process.env.WHISPER_DEV_MODEL_PATH;
  const model = configured?.startsWith('~/') ? join(homedir(), configured.slice(2)) : configured;
  if (!model || !existsSync(model) || !statSync(model).isFile()) {
    throw new Error('WHISPER_DEV_MODEL_PATH must name an existing ggml model file');
  }
  requireExecutable('whisper-server');
  requireExecutable('ffmpeg');
  const child = spawn(
    'whisper-server',
    ['-m', model, '--host', '127.0.0.1', '--port', '8178', '--convert', '-l', 'auto'],
    { stdio: 'inherit', detached: true },
  );
  let stopping = false;
  const forwards = new Map(
    ['SIGINT', 'SIGTERM', 'SIGHUP'].map((signal) => [
      signal,
      () => {
        if (stopping) return;
        stopping = true;
        try {
          process.kill(-child.pid, signal);
        } catch (error) {
          if (error.code !== 'ESRCH') throw error;
        }
      },
    ]),
  );
  for (const [signal, handler] of forwards) process.on(signal, handler);
  child.once('error', (error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
  child.once('exit', (code, signal) => {
    for (const [name, handler] of forwards) process.off(name, handler);
    process.exitCode = code ?? (signal ? 1 : 0);
  });
  return child;
};

if (import.meta.main) {
  try {
    startWhisper();
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
