import js from '@eslint/js';
import boundaries from 'eslint-plugin-boundaries';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import { defineConfig, globalIgnores } from 'eslint/config';
import tseslint from 'typescript-eslint';

const allowed = {
  app: ['features', 'components', 'lib'],
  features: ['components', 'api', 'lib'],
  components: ['lib'],
  api: ['lib'],
  lib: [],
  test: ['lib'],
};
const layers = Object.keys(allowed).filter((type) => type !== 'test');

export default defineConfig([
  globalIgnores(['dist', 'src/api/generated/**', '.api-check/**']),
  {
    files: ['**/*.{js,mjs}'],
    extends: [js.configs.recommended],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, tseslint.configs.strictTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },
  {
    files: ['src/**/*.tsx'],
    extends: [reactHooks.configs.flat.recommended, reactRefresh.configs.vite],
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { boundaries },
    settings: {
      'import/resolver': { typescript: { project: './tsconfig.app.json' } },
      'boundaries/ignore': ['src/vite-env.d.ts'],
      'boundaries/elements': [
        ...layers.map((type) => ({ type, pattern: `src/${type}` })),
        { type: 'test', pattern: 'src/test' },
      ],
      'boundaries/files': [{ category: 'entry', pattern: 'src/main.tsx' }],
    },
    rules: {
      'boundaries/no-unknown-files': 'error',
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          policies: [
            ...Object.entries(allowed).map(([from, to]) => ({
              from: { element: { type: from } },
              allow: { to: { element: { type: to } } },
            })),
            {
              from: { file: { categories: 'entry' } },
              allow: { to: { element: { type: ['app', 'lib'] } } },
            },
          ],
        },
      ],
    },
  },
]);
