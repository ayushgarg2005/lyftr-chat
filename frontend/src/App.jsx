import { useEffect, useRef, useState } from 'react'
import { API_URL, fetchHealth, newSessionId, sendMessage } from './api.js'

const MAX_CHARS = 4000

const SIMULATION_PRESETS = [
  { label: 'Off', value: '' },
  { label: 'key_1 → 429', value: 'key_1=429' },
  { label: 'key_1 → 500', value: 'key_1=500' },
  { label: 'key_1 → 429, key_2 → 500', value: 'key_1=429,key_2=500' },
  { label: 'all keys → 429', value: '*=429' },
]

function Meta({ meta }) {
  const cached = meta.resolution_source === 'LLM_CACHE'
  return (
    <div className="meta">
      <span className={`badge ${cached ? 'badge-cache' : 'badge-live'}`} data-testid="meta-source">
        {meta.resolution_source}
      </span>
      <span className="meta-item" title="Model">
        <span data-testid="meta-model">{meta.model}</span>
      </span>
      <span className="meta-item" title="Server latency">
        <span data-testid="meta-latency">{meta.latency_ms} ms</span>
      </span>
      <span className="meta-item" title="Tokens for this request only">
        in <span data-testid="meta-input-tokens">{meta.input_tokens}</span>
        {' / '}
        out <span data-testid="meta-output-tokens">{meta.output_tokens}</span>
      </span>
      <span className="meta-item meta-dim" title="Key that answered">
        {meta.key_id ?? 'no key (cache)'}
        {meta.fallback_attempts > 0 && (
          <span className="fallback"> · {meta.fallback_attempts} fallback{meta.fallback_attempts > 1 ? 's' : ''}</span>
        )}
      </span>
    </div>
  )
}

function Message({ msg }) {
  if (msg.role === 'user') {
    return (
      <div className="row row-user">
        <div className="bubble bubble-user">{msg.content}</div>
      </div>
    )
  }
  if (msg.role === 'error') {
    return (
      <div className="row">
        <div className="bubble bubble-error" role="alert" data-testid="error-message">
          {msg.content}
        </div>
      </div>
    )
  }
  return (
    <div className="row">
      <div className="bubble bubble-assistant" data-testid="assistant-message">
        <div className="reply-text">{msg.content}</div>
        <Meta meta={msg.metadata} />
      </div>
    </div>
  )
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [simulate, setSimulate] = useState('')
  const [pending, setPending] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [health, setHealth] = useState(null)
  const listRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    fetchHealth().then(setHealth)
  }, [])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, pending])

  async function handleSend() {
    const text = input
    if (!text.trim() || pending) return

    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: text.trim() }])
    setPending(true)

    const result = await sendMessage({ sessionId, message: text, systemPrompt, simulate })

    setMessages((prev) => [
      ...prev,
      result.ok
        ? { role: 'assistant', content: result.data.reply, metadata: result.data.metadata }
        : { role: 'error', content: result.error },
    ])
    setPending(false)
    inputRef.current?.focus()
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  function startNewChat() {
    setSessionId(newSessionId())
    setMessages([])
    setInput('')
    inputRef.current?.focus()
  }

  const tooLong = input.trim().length > MAX_CHARS
  const statusOk = health?.ok

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true" />
          <div>
            <h1>Chat</h1>
            <div className="sub">
              session <code>{sessionId}</code>
            </div>
          </div>
        </div>
        <div className="actions">
          <span className={`status ${statusOk ? 'up' : health ? 'down' : ''}`} title={API_URL}>
            {health === null
              ? 'checking…'
              : statusOk
                ? `${health.data.keys_configured} keys · cache ${health.data.cache_enabled ? 'on' : 'off'}`
                : 'backend offline'}
          </span>
          <button className="btn" onClick={() => setShowSettings((s) => !s)} aria-expanded={showSettings}>
            Settings
          </button>
          <button className="btn" onClick={startNewChat}>
            New chat
          </button>
        </div>
      </header>

      {showSettings && (
        <section className="settings">
          <label className="field">
            <span>System prompt (optional, applies to this session)</span>
            <textarea
              data-testid="system-prompt-input"
              rows={2}
              value={systemPrompt}
              placeholder="e.g. You are a concise assistant that answers in one paragraph."
              onChange={(e) => setSystemPrompt(e.target.value)}
            />
          </label>
          <label className="field field-small">
            <span>Simulate key failure (X-Simulate-Failure)</span>
            <select value={simulate} onChange={(e) => setSimulate(e.target.value)}>
              {SIMULATION_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </section>
      )}

      <main className="messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty">
            <p>Ask anything. Each reply shows where it came from, how long it took and how many tokens it used.</p>
            <p className="hint">Send the same first message in a new chat to see a cache hit.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <Message key={i} msg={m} />
        ))}
        {pending && (
          <div className="row">
            <div className="bubble bubble-assistant typing" aria-label="waiting for reply">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </main>

      <footer className="composer">
        {simulate && <div className="sim-note">Failure simulation on: {simulate}</div>}
        <div className="composer-box">
          <textarea
            ref={inputRef}
            data-testid="chat-input"
            rows={1}
            value={input}
            placeholder="Type a message…  (Enter to send, Shift+Enter for a new line)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
          />
          <button
            className="btn btn-primary"
            data-testid="send-button"
            onClick={handleSend}
            disabled={pending || !input.trim()}
          >
            {pending ? 'Sending…' : 'Send'}
          </button>
        </div>
        <div className={`counter ${tooLong ? 'over' : ''}`}>
          {input.trim().length} / {MAX_CHARS}
        </div>
      </footer>
    </div>
  )
}
