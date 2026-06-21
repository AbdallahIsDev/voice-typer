import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// ERR-ERR-005 (fix): explicit null check instead of `!` non-null assertion.
// If the root element is missing, fail loudly with a clear error message
// instead of crashing inside ReactDOM.createRoot.
const rootEl = document.getElementById('root')
if (!rootEl) throw new Error('Root element #root not found in index.html')

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
