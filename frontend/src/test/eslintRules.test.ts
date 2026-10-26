// @vitest-environment node
import { ESLint } from 'eslint';
import { expect, test } from 'vitest';

test('enforces arrow-style declarations in source files', async () => {
  const [result] = await new ESLint().lintText('export function f() {}', {
    filePath: 'src/lib/cn.ts',
  });
  expect(result?.messages.map((message) => message.ruleId)).toContain('func-style');
});

test('requires keyboard access and accessible control names', async () => {
  const linter = new ESLint();
  const [interactive] = await linter.lintText(
    'export const Probe = () => <div onClick={() => {}} />;',
    { filePath: 'src/app/App.tsx' },
  );
  expect(interactive?.messages.map((message) => message.ruleId)).toContain(
    'jsx-a11y/click-events-have-key-events',
  );
  const [unnamed] = await linter.lintText('export const Probe = () => <button><svg /></button>;', {
    filePath: 'src/app/App.tsx',
  });
  expect(unnamed?.messages.map((message) => message.ruleId)).toContain(
    'jsx-a11y/control-has-associated-label',
  );
});
