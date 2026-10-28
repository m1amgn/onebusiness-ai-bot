import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { assertOwnedProject } from './stack-runtime.mjs';

const MODEL = 'base';
const MODEL_DIGEST = '60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe';
const BASE_ENV = { WHISPER_MODEL: MODEL };
const STARTUP_TIMEOUT = 12 * 60 * 1000;

const requireResult = (condition, message) => {
  if (!condition) throw new Error(`Whisper acceptance: ${message}`);
};

const elapsed = (start) => Math.round(performance.now() - start);

const removeModelVolume = async (ctx) => {
  await ctx.compose(['rm', '--stop', '--force', 'whisper'], {
    env: BASE_ENV,
    timeout: 30000,
    label: 'remove owned whisper service before its models volume',
  });
  const volume = `${ctx.project}_whisper-models`;
  const listed = await ctx.docker(
    ['volume', 'ls', '--filter', `name=^${volume}$`, '--format', '{{.Name}}'],
    { timeout: 10000, label: 'find owned whisper models volume' },
  );
  requireResult(
    listed.stdout.trim() === '' || listed.stdout.trim() === volume,
    'unexpected models volume listing',
  );
  if (listed.stdout.trim() === '') return;
  await ctx.docker(['volume', 'rm', volume], {
    timeout: 30000,
    label: 'remove only the owned whisper models volume',
  });
};

const modelState = async (ctx) => {
  const helper = `${ctx.project}-whisper-model-check`;
  const script = `
set -eu
shopt -s nullglob
models=(/models/ggml-*.bin)
partial=/models/ggml-base.bin.part
target=/models/ggml-base.bin
bytes=0
partial_digest=''
final_digest=''
if [[ -f "$partial" ]]; then
  bytes=$(stat -c %s "$partial")
  read -r partial_digest _ < <(sha256sum "$partial")
fi
if [[ -f "$target" ]]; then
  read -r final_digest _ < <(sha256sum "$target")
fi
printf '{"finalModels":%d,"partialBytes":%s,"partialDigest":"%s","finalDigest":"%s"}\\n' \
  "\${#models[@]}" "$bytes" "$partial_digest" "$final_digest"
`;
  let result;
  try {
    result = await ctx.docker(
      [
        'run',
        '--rm',
        '--name',
        helper,
        '--label',
        `com.docker.compose.project=${ctx.project}`,
        '--network',
        'none',
        '--read-only',
        '--cap-drop',
        'ALL',
        '--security-opt',
        'no-new-privileges:true',
        '--mount',
        `type=volume,source=${ctx.project}_whisper-models,target=/models,readonly`,
        '--entrypoint',
        'bash',
        `${ctx.project}-whisper`,
        '-c',
        script,
      ],
      { timeout: 30000, label: 'inspect only owned whisper model files' },
    );
  } finally {
    await ctx.docker(['rm', '--force', helper], {
      timeout: 10000,
      allowFailure: true,
      label: 'remove owned whisper model inspector',
    });
  }
  let state;
  try {
    state = JSON.parse(result.stdout);
  } catch {
    throw new Error('Whisper acceptance: invalid model inspection result');
  }
  requireResult(
    state !== null &&
      typeof state === 'object' &&
      Number.isSafeInteger(state.finalModels) &&
      state.finalModels >= 0 &&
      Number.isSafeInteger(state.partialBytes) &&
      state.partialBytes >= 0 &&
      typeof state.partialDigest === 'string' &&
      /^(?:[a-f0-9]{64})?$/.test(state.partialDigest) &&
      typeof state.finalDigest === 'string' &&
      /^(?:[a-f0-9]{64})?$/.test(state.finalDigest),
    'invalid model inspection fields',
  );
  return state;
};

const startWhisper = (ctx) =>
  ctx.compose(['up', '--no-build', '--no-deps', '-d', 'whisper'], {
    env: BASE_ENV,
    timeout: 45000,
    label: 'start owned whisper service with the base model',
  });

const interruptDownload = async (ctx) => {
  // Freeze the actual downloader as soon as it has bytes. This closes the gap
  // between observing a partial file and asking Docker to kill the container.
  const script = `
set -eu
deadline=$((SECONDS + 120))
partial=/models/ggml-base.bin.part
while (( SECONDS < deadline )); do
  [[ ! -f /models/ggml-base.bin ]] || exit 44
  if [[ -s "$partial" ]]; then
    for process in /proc/[0-9]*; do
      [[ -r "$process/comm" && -r "$process/cmdline" ]] || continue
      IFS= read -r command < "$process/comm" || continue
      [[ "$command" == curl ]] || continue
      arguments=$(tr '\\0' ' ' < "$process/cmdline") || continue
      [[ "$arguments" == *"--output $partial"* ]] || continue
      if kill -STOP "\${process##*/}" 2>/dev/null; then
        [[ ! -f /models/ggml-base.bin && -s "$partial" ]] || exit 44
        stat -c %s "$partial"
        exit 0
      fi
    done
  fi
  sleep 0.05
done
exit 45
`;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    await removeModelVolume(ctx);
    const start = performance.now();
    await startWhisper(ctx);
    const container = await ctx.id('whisper');
    const watched = await ctx.docker(['exec', container, 'bash', '-c', script], {
      timeout: 130000,
      allowFailure: true,
      label: 'wait for real whisper model bytes and interrupt the downloader',
    });
    await ctx.compose(['kill', '--signal', 'SIGKILL', 'whisper'], {
      env: BASE_ENV,
      timeout: 30000,
      label: 'kill only the owned whisper service during download',
    });
    requireResult(
      watched.code === 0 || watched.code === 44,
      'the model download did not produce an interruptible partial file within 120 seconds',
    );
    const state = await modelState(ctx);
    if (
      watched.code === 0 &&
      state.finalModels === 0 &&
      state.partialBytes > 0 &&
      state.partialDigest !== MODEL_DIGEST
    ) {
      return {
        attempts: attempt,
        bytes: state.partialBytes,
        elapsedMs: elapsed(start),
        noFinalModel: true,
      };
    }
    // A transfer can finish before the watcher attaches. Retry on a new empty
    // owned volume; a completed download never counts as an interruption.
  }
  throw new Error('Whisper acceptance: the model download finished before all three interruptions');
};

const inference = async (ctx, container, file, contentType) => {
  const result = await ctx.docker(
    [
      'exec',
      container,
      'curl',
      '--silent',
      '--show-error',
      '--connect-timeout',
      '5',
      '--max-time',
      '300',
      '--request',
      'POST',
      '--form',
      `file=@${file};type=${contentType}`,
      '--form',
      'response_format=json',
      '--form',
      'temperature=0',
      '--write-out',
      '\n%{http_code}',
      'http://127.0.0.1:8080/inference',
    ],
    { timeout: 310000, label: 'post a non-secret acceptance fixture to whisper inference' },
  );
  const status = result.stdout.match(/\n(\d{3})\s*$/);
  requireResult(status !== null, 'inference did not return an HTTP status');
  let body;
  try {
    body = JSON.parse(result.stdout.slice(0, status.index));
  } catch {
    throw new Error('Whisper acceptance: inference did not return JSON');
  }
  requireResult(body !== null && typeof body === 'object', 'invalid inference response shape');
  return { status: Number(status[1]), body };
};

export const checkWhisper = async (ctx) => {
  assertOwnedProject(ctx.project);
  await ctx.compose(['rm', '--stop', '--force', 'whisper'], {
    env: BASE_ENV,
    timeout: 30000,
    label: 'remove owned whisper service before invalid-model check',
  });
  const unknownStart = performance.now();
  const unknown = await ctx.compose(
    ['up', '--no-build', '--no-deps', '-d', '--wait', '--wait-timeout', '20', 'whisper'],
    {
      env: { WHISPER_MODEL: 'taskbot-acceptance-unknown' },
      timeout: 45000,
      allowFailure: true,
      label: 'verify unknown WHISPER_MODEL makes bounded up --wait fail',
    },
  );
  const unknownMs = elapsed(unknownStart);
  const logs = await ctx.compose(['logs', '--no-color', '--tail', '20', 'whisper'], {
    timeout: 10000,
    label: 'check whisper failure log names WHISPER_MODEL without printing it',
  });
  requireResult(
    Number.isInteger(unknown.code) && unknown.code > 0,
    'up --wait did not exit non-zero for an unknown model',
  );
  requireResult(
    /WHISPER_MODEL/.test(`${logs.stdout}\n${logs.stderr}`),
    'the unknown-model log did not name WHISPER_MODEL',
  );

  await removeModelVolume(ctx);
  const startupStart = performance.now();
  await startWhisper(ctx);
  await ctx.waitHealthy('whisper', STARTUP_TIMEOUT);
  const startupMs = elapsed(startupStart);
  const coldState = await modelState(ctx);
  requireResult(
    coldState.finalModels === 1 && coldState.finalDigest === MODEL_DIGEST,
    'the uninterrupted cold start did not verify the base model',
  );
  const coldPeak = await ctx.docker(
    ['exec', await ctx.id('whisper'), 'cat', '/sys/fs/cgroup/memory.peak'],
    { timeout: 10000, label: 'record cold whisper memory peak' },
  );
  const coldPeakBytes = Number(coldPeak.stdout.trim());
  requireResult(coldPeakBytes > 0, 'cold whisper memory peak missing');
  if (ctx.measurements)
    ctx.measurements.whisper = Math.max(ctx.measurements.whisper ?? 0, coldPeakBytes);

  const interruptedDownload = await interruptDownload(ctx);
  const recoveryStart = performance.now();
  await startWhisper(ctx);
  await ctx.waitHealthy('whisper', STARTUP_TIMEOUT);
  const recoveryStartupMs = elapsed(recoveryStart);
  const state = await modelState(ctx);
  requireResult(
    state.finalModels === 1 && state.finalDigest === MODEL_DIGEST && state.partialDigest === '',
    'the next start did not replace the partial file with the verified base model',
  );

  const container = await ctx.id('whisper');
  const fixtures = '/tmp/taskbot-acceptance-whisper';
  const audio = `${fixtures}/voice.ogg`;
  const nonAudio = `${fixtures}/not-audio.txt`;
  try {
    // Docker's archive API cannot resolve this hardened container's tmpfs paths.
    // Transfer through the configured non-root process, which owns the directory.
    await ctx.docker(['exec', container, 'mkdir', '-p', fixtures], {
      timeout: 10000,
      label: 'create a non-root-owned whisper fixture directory in tmpfs',
    });
    await ctx.docker(['exec', '-i', container, 'bash', '-c', `cat > '${audio}'`], {
      input: await readFile(join(ctx.root, 'backend/tests/fixtures/voice-en.ogg')),
      timeout: 10000,
      label: 'stream only the public voice fixture into owned whisper as its non-root user',
    });
    await ctx.docker(
      ['exec', container, 'bash', '-c', `printf 'acceptance non-audio fixture\\n' > '${nonAudio}'`],
      { timeout: 10000, label: 'create a fixed non-audio fixture in owned whisper tmpfs' },
    );
    const inferenceStart = performance.now();
    const transcript = await inference(ctx, container, audio, 'audio/ogg');
    const inferenceMs = elapsed(inferenceStart);
    requireResult(
      transcript.status === 200 &&
        typeof transcript.body.text === 'string' &&
        transcript.body.text.trim().length > 0,
      'the real voice fixture did not produce non-empty text',
    );
    const rejected = await inference(ctx, container, nonAudio, 'text/plain');
    requireResult(
      rejected.status === 500 &&
        typeof rejected.body.error === 'string' &&
        rejected.body.error.toLowerCase().includes('ffmpeg conversion failed'),
      'the non-audio response did not match the S5 unsupported_audio mapping',
    );
    return {
      model: MODEL,
      unknownModel: { exitCode: unknown.code, elapsedMs: unknownMs, settingNamed: true },
      interruptedDownload,
      startupMs,
      recoveryStartupMs,
      coldPeakBytes,
      inferenceMs,
      transcriptCharacters: transcript.body.text.trim().length,
      unsupportedAudio: { status: rejected.status, kind: 'unsupported_audio' },
    };
  } finally {
    await ctx.docker(['exec', container, 'rm', '-rf', fixtures], {
      timeout: 10000,
      allowFailure: true,
      label: 'remove whisper acceptance fixtures from owned tmpfs',
    });
  }
};
