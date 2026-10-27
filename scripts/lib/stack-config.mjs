import assert from 'node:assert/strict';
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { buildInfraCommand } from '../infra.mjs';
import { runCommand } from './stack-runtime.mjs';

const DOCKER_ENVIRONMENT = [
  'PATH',
  'HOME',
  'DOCKER_HOST',
  'DOCKER_CONTEXT',
  'DOCKER_CONFIG',
  'TMPDIR',
];
const POSTGRES = {
  POSTGRES_USER: 'SyntheticUser',
  POSTGRES_PASSWORD: 'SyntheticPassword',
  POSTGRES_DB: 'SyntheticDatabase',
};

export const checkSharedConfig = async (ctx, { runner = runCommand } = {}) => {
  const root = await mkdtemp(join(ctx.scratch, 'shared-config-'));
  try {
    for (const name of [
      'docker-compose.yml',
      'docker/dev-infra.ports.yml',
      'scripts/empty.env',
      '.env.example',
    ]) {
      await mkdir(dirname(join(root, name)), { recursive: true });
      await copyFile(join(ctx.root, name), join(root, name));
    }
    const env = Object.fromEntries(
      Object.entries(ctx.env).filter(([key]) => DOCKER_ENVIRONMENT.includes(key)),
    );
    const hashes = {};
    for (const name of ['absent', 'example', 'custom', 'shell']) {
      await rm(join(root, '.env'), { force: true });
      if (name === 'example') await copyFile(join(root, '.env.example'), join(root, '.env'));
      if (name === 'custom') {
        await writeFile(
          join(root, '.env'),
          Object.entries(POSTGRES)
            .map(([key, value]) => `${key}=${value}\n`)
            .join(''),
          { mode: 0o600 },
        );
      }
      const built = buildInfraCommand(
        'up',
        { ...env, ...(name === 'shell' ? POSTGRES : {}) },
        root,
      );
      const prefix = built.args.slice(0, built.args.indexOf('up'));
      hashes[name] = {};
      for (const service of ['postgres', 'redis']) {
        const label = `docker compose config --hash ${service} (shared ${name})`;
        ctx.commands?.push(label);
        console.log(`$ ${label}`);
        // ctx.docker overlays ctx.env, which would reintroduce acceptance credentials.
        const { stdout } = await runner('docker', [...prefix, 'config', '--hash', service], {
          env: built.env,
          timeout: 30000,
          signal: ctx.signal,
          label,
        });
        const match = stdout.trim().match(new RegExp(`^${service}\\s+([a-f0-9]{64})$`));
        assert.ok(match, `Shared ${service} config hash missing for ${name}`);
        hashes[name][service] = match[1];
        if (name !== 'absent') {
          assert.equal(
            hashes[name][service],
            hashes.absent[service],
            `Shared ${service} config changed with ${name} credentials`,
          );
        }
      }
    }
    return { cases: Object.keys(hashes).length, hashes };
  } finally {
    await rm(root, { recursive: true, force: true });
  }
};

const addImage = (images, ref) => {
  const match = ref.match(/^([^\s:@]+(?::\d+)?(?:\/[^\s:@]+)*):([^\s@]+)@sha256:([a-f0-9]{64})$/);
  assert.ok(match, 'An external image needs a literal version tag and SHA-256 digest');
  const [, repository, tag] = match;
  let architectures = ['amd64', 'arm64'];
  if (repository === 'ghcr.io/ggml-org/whisper.cpp') {
    assert.ok(['main', 'main-arm64'].includes(tag), 'Unexpected whisper.cpp image tag');
    architectures = [tag === 'main' ? 'amd64' : 'arm64'];
  } else {
    // PostgreSQL 10+ uses major.minor for an exact patch release.
    const version =
      repository === 'postgres' ? /^\d+\.\d+(?:-[\w.-]+)?$/ : /^\d+\.\d+\.\d+(?:-[\w.-]+)?$/;
    assert.ok(version.test(tag), 'A versioned image needs an exact patch release tag');
  }
  images.set(ref, architectures);
};

export const checkImagePins = async (ctx) => {
  const images = new Map();
  for (const name of ['backend', 'web', 'whisper']) {
    const text = await readFile(join(ctx.root, `docker/${name}.Dockerfile`), 'utf8');
    const stages = new Set();
    for (const line of text.split('\n').filter((line) => /^\s*FROM\s/i.test(line))) {
      const match = line.match(/^\s*FROM(?:\s+--platform=\S+)?\s+(\S+)(?:\s+AS\s+([\w-]+))?\s*$/i);
      assert.ok(match, 'Unsupported Dockerfile FROM declaration');
      const [, ref, stage] = match;
      if (ref === 'whisper-${TARGETARCH}') {
        assert.ok(
          name === 'whisper' && stages.has('whisper-amd64') && stages.has('whisper-arm64'),
          'Whisper architecture selection needs both pinned stages',
        );
      } else if (!stages.has(ref.toLowerCase())) addImage(images, ref);
      if (stage) stages.add(stage.toLowerCase());
    }
  }
  const compose = await readFile(join(ctx.root, 'docker-compose.yml'), 'utf8');
  for (const line of compose.split('\n').filter((line) => /^\s*image:/.test(line))) {
    const ref = line.slice(line.indexOf(':') + 1).trim();
    if (/^\$\{COMPOSE_PROJECT_NAME:-taskbot-app\}-(backend|web|whisper)$/.test(ref)) continue;
    addImage(images, ref);
  }
  assert.ok(images.size > 0, 'No pinned external images found');
  const platforms = {};
  for (const [ref, architectures] of images) {
    const { stdout } = await ctx.docker(['buildx', 'imagetools', 'inspect', ref, '--raw'], {
      timeout: 120000,
      label: `docker buildx imagetools inspect ${ref} --raw`,
    });
    const index = JSON.parse(stdout);
    assert.ok(Array.isArray(index.manifests), 'A pinned image must identify a platform index');
    platforms[ref] = [
      ...new Set(
        index.manifests
          .filter((item) => item.platform?.os === 'linux')
          .map((item) => `linux/${item.platform.architecture}`),
      ),
    ].sort();
    for (const architecture of architectures) {
      assert.ok(
        platforms[ref].includes(`linux/${architecture}`),
        `Pinned image is missing linux/${architecture}`,
      );
    }
  }
  return { refs: [...images.keys()], platforms };
};
