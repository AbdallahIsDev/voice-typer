/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import { resolve } from 'path';

// BUILD-N11: vitest configuration for the renderer.
// Uses jsdom so React Testing Library can mount components that
// depend on `window` / `document`. The alias block mirrors
// vite.config.ts so imports like `#components` work in tests.
export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/renderer/src/test-setup.ts'],
    include: ['src/renderer/src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/renderer/src/**/*.{ts,tsx}'],
      exclude: ['**/*.test.{ts,tsx}', '**/*.spec.{ts,tsx}'],
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src/renderer/src'),
      '#components': resolve(__dirname, 'src/renderer/src/components/index.ts'),
      '#ui': resolve(__dirname, 'src/renderer/src/components/ui'),
      '#lib': resolve(__dirname, 'src/renderer/src/lib/index.ts'),
      '#utils': resolve(__dirname, 'src/renderer/src/lib/utils.ts'),
      '#hooks': resolve(__dirname, 'src/renderer/src/hooks/index.ts'),
    },
  },
});
