// The API sets the SSE `event:` field to the StreamEvent type, so `onmessage`
// never fires -- every type needs its own listener.
const EVENT_TYPES = [
  'run_started', 'node_started', 'node_finished', 'clarify',
  'hypothesis', 'repro_attempt', 'fix_attempt', 'verdict', 'error',
]

export async function startRun({ rawText, repoPath }) {
  const response = await fetch('/runs', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ raw_text: rawText, repo_path: repoPath }),
  })
  if (!response.ok) {
    throw new Error(`POST /runs failed: ${response.status} ${await response.text()}`)
  }
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
  const source = new EventSource(`/runs/${runId}/events`)
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
    onError(new Error('the event stream dropped before the run finished'))
  }

  return close
}

export async function fetchRecord(runId) {
  const response = await fetch(`/runs/${runId}`)
  if (response.status === 202) return null // still running
  if (!response.ok) throw new Error(`GET /runs/${runId} failed: ${response.status}`)
  return response.json()
}
