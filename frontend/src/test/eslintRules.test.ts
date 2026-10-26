// @vitest-environment node
import { ESLint } from 'eslint';
import tseslint from 'typescript-eslint';
import { expect, test } from 'vitest';

// These rules inspect syntax. Full verification runs type-aware ESLint separately.
const createRuleLinter = (): ESLint =>
  new ESLint({ overrideConfig: tseslint.configs.disableTypeChecked });

test('enforces arrow-style declarations in source files', { timeout: 30_000 }, async () => {
  const [result] = await createRuleLinter().lintText('export function f() {}', {
    filePath: 'src/lib/cn.ts',
  });
  expect(result?.messages.map((message) => message.ruleId)).toContain('func-style');
});

test('requires keyboard access and accessible control names', { timeout: 30_000 }, async () => {
  const linter = createRuleLinter();
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
