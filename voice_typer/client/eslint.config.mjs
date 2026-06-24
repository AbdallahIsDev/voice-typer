// BUILD-N09: ESLint 9 flat config.
// Lints TypeScript + React with the rules recommended by
// @typescript-eslint and eslint-plugin-react-hooks. Prettier handles
// formatting concerns (see .prettierrc); this config explicitly
// disables eslint rules that conflict with prettier via
// eslint-config-prettier.
//
// ERR-LINT-001 (fix): added globals for Node.js, Electron, and browser
// APIs so `no-undef` doesn't flag `__dirname`, `Buffer`, `Electron`,
// `MouseEvent`, etc. Also disabled `no-namespace` (legacy namespace
// syntax in main/index.ts is intentional for Electron main process)
// and relaxed `no-require-imports` for the main process (Electron
// uses `require()` for native modules at runtime).
import js from '@eslint/js';
import tseslint from '@typescript-eslint/eslint-plugin';
import tsparser from '@typescript-eslint/parser';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import prettier from 'eslint-config-prettier';

// ERR-LINT-001: comprehensive globals for Node + Electron + browser.
const ALL_GLOBALS = {
  // Node.js globals
  __dirname: 'readonly',
  __filename: 'readonly',
  Buffer: 'readonly',
  setImmediate: 'readonly',
  clearImmediate: 'readonly',
  require: 'readonly',
  module: 'readonly',
  exports: 'readonly',
  global: 'readonly',
  process: 'readonly',
  console: 'readonly',
  setTimeout: 'readonly',
  clearTimeout: 'readonly',
  setInterval: 'readonly',
  clearInterval: 'readonly',
  // Browser/DOM globals (used in renderer)
  window: 'readonly',
  document: 'readonly',
  fetch: 'readonly',
  MouseEvent: 'readonly',
  KeyboardEvent: 'readonly',
  HTMLElement: 'readonly',
  HTMLSpanElement: 'readonly',
  HTMLInputElement: 'readonly',
  HTMLButtonElement: 'readonly',
  HTMLDivElement: 'readonly',
  Node: 'readonly',
  localStorage: 'readonly',
  navigator: 'readonly',
  requestAnimationFrame: 'readonly',
  cancelAnimationFrame: 'readonly',
  // NEW-UX-029: Web Audio API globals for the sound feedback cue.
  AudioContext: 'readonly',
  webkitAudioContext: 'readonly',
  React: 'readonly',
  NodeJS: 'readonly',
  // Electron globals (used in main + preload)
  Electron: 'readonly',
  ipcRenderer: 'readonly',
  ipcMain: 'readonly',
  contextBridge: 'readonly',
  app: 'readonly',
  BrowserWindow: 'readonly',
  dialog: 'readonly',
  nativeTheme: 'readonly',
  screen: 'readonly',
  session: 'readonly',
  shell: 'readonly',
  Menu: 'readonly',
  clipboard: 'readonly',
  globalShortcut: 'readonly',
  net: 'readonly',
  protocol: 'readonly',
  crashReporter: 'readonly',
};

export default [
  js.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsparser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
      globals: ALL_GLOBALS,
    },
    plugins: {
      '@typescript-eslint': tseslint,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // Defer formatting to prettier
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      // ERR-LINT-001: allow namespace syntax (used in main/index.ts)
      '@typescript-eslint/no-namespace': 'off',
      // ERR-LINT-001: allow require() in main process
      '@typescript-eslint/no-require-imports': 'off',
      // ERR-LINT-001: allow empty catch blocks (used for intentional
      // error suppression in IPC handlers)
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    ignores: [
      'out/**',
      'dist/**',
      'src/renderer/dist/**',
      'node_modules/**',
      '*.config.*',
    ],
  },
  prettier,
];
