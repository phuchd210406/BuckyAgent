// Empty in dev: Vite proxies /runs to the API, so the browser stays same-origin.
// Set VITE_API_BASE at build time to point a deployed frontend (Vercel) at a
// backend somewhere else (an ngrok tunnel, say), which needs CORS on that side.
export const API_BASE = (import.meta.env?.VITE_API_BASE || '').replace(/\/$/, '')

// The API sets the SSE `event:` field to the StreamEvent type, so `onmessage`
// never fires -- every type needs its own listener.
const EVENT_TYPES = [
  'run_started', 'node_started', 'node_finished', 'clarify',
  'hypothesis', 'repro_attempt', 'fix_attempt', 'verdict', 'error',
]

/**
 * Start a run against either a GitHub repository or a local demo checkout.
 *
 * Exactly one of `repoUrl` and `repoPath` is sent: the API refuses both, and a
 * missing one is the difference between "clone this" and "use what is on disk".
 */
export async function startRun({ rawText, repoUrl, repoPath, repoRef }) {
  const body = { raw_text: rawText }
  if (repoUrl?.trim()) {
    body.repo_url = repoUrl.trim()
    if (repoRef?.trim()) body.repo_ref = repoRef.trim()
  } else {
    body.repo_path = repoPath
  }

  const response = await fetch(`${API_BASE}/runs`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`the backend refused the run (${response.status}): ${await detail(response)}`)
  }
  return response.json()
}

/** FastAPI puts the readable half of a 4xx in `detail`; fall back to the body. */
async function detail(response) {
  const text = await response.text()
  try {
    const parsed = JSON.parse(text)
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) return parsed.detail.map((d) => d.msg).join('; ')
  } catch {
    /* not JSON; the raw body is the best we have */
  }
  return text
}

/**
 * What the backend would really do right now: which provider, which model, and
 * whether a model is called at all. Fetched once on load so the page can say so
 * rather than implying a live run it is not going to make.
 */
export async function fetchConfig() {
  const response = await fetch(`${API_BASE}/config`)
  if (!response.ok) throw new Error(`GET /config failed: ${response.status}`)
  return response.json()
}

/**
 * Follow a run. Returns a close function.
 *
 * The stream is closed from HERE the moment the verdict lands. EventSource
 * reconnects automatically when a connection ends, and the API replays its
 * backlog to every new subscriber, so leaving it open would loop the whole run
 * forever.
 */
export function followRun(runId, { onEvent, onDone, onError }) {
  const source = new EventSource(`${API_BASE}/runs/${runId}/events`)
  let finished = false

  const close = () => {
    finished = true
    source.close()
  }

  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (message) => {
      const event = JSON.parse(message.data)
      onEvent(event)
      if (event.type === 'verdict') {
        close()
        onDone(event)
        return
      }
      // A run that died emits a terminal error and no verdict. Without this the
      // stream just ends, EventSource calls it a dropped connection, and the UI
      // blames the network for something the run already explained.
      if (event.type === 'error' && event.payload?.terminal) {
        close()
        onError(new Error(event.payload.error || 'the run stopped'))
      }
    })
  }

  source.onerror = () => {
    if (finished) return // a clean close after the verdict
    close()
    // Not a crash and not the end of the run: the server keeps going and keeps
    // the record. Reconnecting replays the whole story from the first event.
    onError({ kind: 'connection_lost', runId })
  }

  return close
}

/**
 * Build the query that names a repository, for the browsing endpoints.
 *
 * The API takes `repo_url` OR `repo_path` and refuses both, so this is the one
 * place that decides which of the two the form is currently describing.
 */
export function repoQuery({ source, repoUrl, repoRef, repoPath }) {
  const params = new URLSearchParams()
  if (source === 'github') {
    if (!repoUrl?.trim()) return null
    params.set('repo_url', repoUrl.trim())
    if (repoRef?.trim()) params.set('repo_ref', repoRef.trim())
  } else {
    if (!repoPath) return null
    params.set('repo_path', repoPath)
  }
  return params
}

/** Every readable file in the repository, plus which one to open first. */
export async function fetchTree(target) {
  const params = repoQuery(target)
  if (!params) return null
  const response = await fetch(`${API_BASE}/repos/tree?${params}`)
  if (!response.ok) throw new Error(await detail(response))
  return response.json()
}

/** One file's text. `path` is repo-relative; the backend checks containment. */
export async function fetchFile(target, path) {
  const params = repoQuery(target)
  if (!params) return null
  params.set('path', path)
  const response = await fetch(`${API_BASE}/repos/file?${params}`)
  if (!response.ok) throw new Error(await detail(response))
  return response.json()
}

export async function fetchRecord(runId) {
  const response = await fetch(`${API_BASE}/runs/${runId}`)
  if (response.status === 202) return null // still running
  if (!response.ok) throw new Error(`GET /runs/${runId} failed: ${response.status}`)
  return response.json()
}
