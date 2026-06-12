import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwind from '@tailwindcss/vite'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/main/index.ts') }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/preload/index.ts') }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    build: {
      rollupOptions: {
        input: { index: resolve(__dirname, 'src/renderer/index.html') }
      }
    },
    plugins: [react(), tailwind()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src/renderer/src'),
        '#components': resolve(__dirname, 'src/renderer/src/components/index.ts'),
        '#ui': resolve(__dirname, 'src/renderer/src/components/ui'),
        '#lib': resolve(__dirname, 'src/renderer/src/lib/index.ts'),
        '#utils': resolve(__dirname, 'src/renderer/src/lib/utils.ts'),
        '#hooks': resolve(__dirname, 'src/renderer/src/hooks/index.ts')
      }
    }
  }
})
