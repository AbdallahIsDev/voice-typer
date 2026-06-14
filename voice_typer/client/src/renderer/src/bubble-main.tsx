import React from 'react'
import ReactDOM from 'react-dom/client'
import { Bubble } from './Bubble'

// Bubble styles share the main renderer's Tailwind build.  We import
// the same index.css so utility classes (gap-4, rounded-2xl, etc.)
// resolve identically.
import './index.css'

console.log('[bubble renderer] mounting')

// Signal the main process that we're mounted and ready to receive
// level events.  Used for diagnostics and to mark the window as
// page-ready in the main process.
;(window as any).bubble?.signalReady?.()

ReactDOM.createRoot(document.getElementById('bubble-root')!).render(
  <React.StrictMode>
    <Bubble />
  </React.StrictMode>,
)
