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
        input: {
          // SEC-026: split the preload into a main-only and a bubble-only
          // build. The bubble renderer gets a much smaller surface (only
          // bubble:level / bubble:show / bubble:hide / bubble:draggable /
          // bubble:position / bubble:drag*), so a compromised bubble can't
          // invoke python.call({type:"quit_app"}) or window_.close().
          index: resolve(__dirname, 'src/preload/index.ts'),
          bubble: resolve(__dirname, 'src/preload/bubble.ts'),
        }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html'),
          bubble: resolve(__dirname, 'src/renderer/bubble.html')
        }
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
