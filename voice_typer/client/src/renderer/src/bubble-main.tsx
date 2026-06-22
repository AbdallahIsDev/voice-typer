import React from 'react'
import ReactDOM from 'react-dom/client'
import { Bubble } from './Bubble'

// Bubble styles share the main renderer's Tailwind build.  We import
// the same index.css so utility classes (gap-4, rounded-2xl, etc.)
// resolve identically.
import './index.css'

console.warn('[bubble renderer] mounting')

// Signal the main process that we're mounted and ready to receive
// level events.  Used for diagnostics and to mark the window as
// page-ready in the main process.
window.bubble?.signalReady?.()

// ERR-ERR-005 (fix): explicit null check instead of `!` non-null assertion.
const bubbleRootEl = document.getElementById('bubble-root')
if (!bubbleRootEl) throw new Error('Bubble root element #bubble-root not found in bubble.html')

ReactDOM.createRoot(bubbleRootEl).render(
  <React.StrictMode>
    <Bubble />
  </React.StrictMode>,
)
