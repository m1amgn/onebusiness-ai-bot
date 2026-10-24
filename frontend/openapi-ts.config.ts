import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: process.env['SCHEMA_IN'] ?? '../backend/openapi.json',
  output: {
    path: process.env['SCHEMA_OUT'] ?? 'src/api/generated',
    postProcess: ['prettier'],
  },
  plugins: ['@hey-api/typescript', 'zod'],
});
