import { useState, useEffect } from 'react'

declare global {
  interface Window {
    python: {
      call: (msg: Record<string, unknown>) => Promise<Record<string, unknown>>
      onEvent: (callback: (msg: Record<string, unknown>) => void) => void
    }
  }
}

function App() {
  const [status, setStatus] = useState('connecting...')
  const [error, setError] = useState('')
  const [ready, setReady] = useState(false)

  useEffect(() => {
    ;(async () => {
      try {
        const res = await window.python.call({ type: 'get_status' })
        setStatus(res.data.status as string)
        setReady(true)
      } catch {
        setStatus('connection failed')
      }
    })()
  }, [])

  useEffect(() => {
    window.python.onEvent((msg) => {
      if (msg.type === 'status_change') setStatus(msg.data.status as string)
      if (msg.type === 'error') setError(msg.data.message as string)
    })
  }, [])

  const toggle = async () => {
    setError('')
    try {
      await window.python.call({ type: 'toggle_dictation' })
    } catch (e) {
      setError('Error: ' + (e as Error).message)
    }
  }

  return (
    <div className="flex items-center justify-center h-screen bg-neutral-950 text-neutral-100">
      <div className="text-center space-y-8">
        <div className="text-lg text-neutral-400">
          Status: <span className="text-neutral-100 font-semibold">{status}</span>
        </div>
        <button
          onClick={toggle}
          disabled={!ready}
          className={`w-20 h-20 rounded-full border-2 transition-all text-3xl
            ${status === 'recording'
              ? 'border-red-500 bg-red-950 text-red-500'
              : 'border-neutral-600 bg-neutral-900 text-neutral-300 hover:border-neutral-400 hover:bg-neutral-800'}
            disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          🎤
        </button>
        {error && <div className="text-red-400 text-sm">{error}</div>}
      </div>
    </div>
  )
}

export default App
