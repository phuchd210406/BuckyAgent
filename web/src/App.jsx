import React, { useEffect, useRef, useState } from 'react'
import RunForm, { SHOPCART_COMPLAINT } from './RunForm.jsx'
import Timeline from './Timeline.jsx'
import CostCounter from './CostCounter.jsx'
import { buildCards, currentStep, latestUsage } from './timeline.js'
import { followRun, startRun } from './api.js'

export default function App() {
  const [rawText, setRawText] = useState(SHOPCART_COMPLAINT)
  const [repoPath, setRepoPath] = useState('fixtures/demo_repos/shopcart')
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [events, setEvents] = useState([])
  const [failure, setFailure] = useState(null) // {kind, message, runId}
  const [runId, setRunId] = useState(null)
  const closeStream = useRef(null)
  const bottom = useRef(null)

  useEffect(() => () => closeStream.current?.(), [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [events.length])

  function follow(id, { replace = false } = {}) {
    if (replace) setEvents([])
    closeStream.current = followRun(id, {
      onEvent: (event) => setEvents((seen) => [...seen, event]),
      onDone: () => setStatus('done'),
      onError: (problem) => {
        setFailure(
          problem.kind === 'connection_lost'
            ? { kind: 'connection_lost', runId: problem.runId }
            : { kind: 'run_failed', message: problem.message },
        )
        setStatus('error')
      },
    })
  }

  async function run() {
    closeStream.current?.()
    setEvents([])
    setFailure(null)
    setRunId(null)
    setStatus('running')
    try {
      const { run_id: id } = await startRun({ rawText, repoPath })
      setRunId(id)
      follow(id)
    } catch (startError) {
      setFailure({ kind: 'unreachable', message: startError.message })
      setStatus('error')
    }
  }

  /** Back to a clean screen without a page reload: the next take starts now. */
  function reset() {
    closeStream.current?.()
    closeStream.current = null
    setEvents([])
    setFailure(null)
    setRunId(null)
    setStatus('idle')
  }

  /** The run kept going and the server kept the record; ask for it again. */
  function reconnect() {
    if (!runId) return
    setFailure(null)
    setStatus('running')
    follow(runId, { replace: true })
  }

  const cards = buildCards(events)

  return (
    <main className="layout">
      <CostCounter usage={latestUsage(events)} status={status} />
      <aside className="col-left">
        <RunForm
          rawText={rawText}
          setRawText={setRawText}
          repoPath={repoPath}
          setRepoPath={setRepoPath}
          onRun={run}
          onReset={reset}
          status={status}
          runId={runId}
        />
      </aside>
      <section className="col-right">
        <Timeline
          cards={cards}
          step={currentStep(cards, status)}
          status={status}
          failure={failure}
          onReconnect={reconnect}
          onRetry={run}
        />
        <div ref={bottom} />
      </section>
    </main>
  )
}
