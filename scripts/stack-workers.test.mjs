import assert from 'node:assert/strict';
import test from 'node:test';
import { checkHardeningAndBudgets } from './lib/stack-workers.mjs';

test('hardening rejects a restart-policy regression in every delivered role', async () => {
  for (const service of [
    'postgres',
    'redis',
    'api',
    'bot',
    'worker-transcribe',
    'worker-notify',
    'web',
    'whisper',
    'migrate',
  ]) {
    const item = {
      Id: 'fixture',
      Config: { User: '10001', Labels: { 'com.docker.compose.service': service } },
      State: { Running: false },
      NetworkSettings: { Networks: {}, Ports: {} },
      HostConfig: {
        RestartPolicy: { Name: service === 'migrate' ? 'always' : 'no' },
        ReadonlyRootfs: true,
        SecurityOpt: ['no-new-privileges:true'],
        CapDrop: ['ALL'],
        Memory: 1024,
        LogConfig: { Type: 'json-file', Config: { 'max-file': '3', 'max-size': '10m' } },
        PortBindings: { '8080/tcp': [{ HostIp: '127.0.0.1', HostPort: '18080' }] },
      },
    };
    const ctx = { port: 18080, capture: async () => [item], compose: async () => ({ stdout: '' }) };
    await assert.rejects(checkHardeningAndBudgets(ctx), /restart policy/);
    item.HostConfig.RestartPolicy.Name = service === 'migrate' ? 'no' : 'unless-stopped';
    await assert.doesNotReject(checkHardeningAndBudgets(ctx));
  }
});
