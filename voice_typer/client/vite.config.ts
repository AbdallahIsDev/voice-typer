import { defineConfig } from 'vite'
import { resolve } from 'path'

// shadcn CLI's framework detection globs for files matching `vite.config.*`
// at the project root. The actual electron-vite config is named
// `electron.vite.config.ts`, which shadcn does not recognize, so it falls
// back to `manual` framework and refuses to apply presets.
//
// This file exposes a Vite-shaped config so shadcn sees a Vite project.
// electron-vite itself only reads `electron.vite.config.ts` and ignores
// this file, so there is no conflict.
export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src/renderer/src'),
      // NEW-TS-016: removed non-existent barrel file aliases
      '#ui': resolve(__dirname, 'src/renderer/src/components/ui'),
      '#utils': resolve(__dirname, 'src/renderer/src/lib/utils.ts')
    }
  }
})
