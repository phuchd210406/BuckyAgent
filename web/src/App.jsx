import React, { useEffect, useRef, useState } from 'react'
import RunForm, { SHOPCART_COMPLAINT } from './RunForm.jsx'
import Timeline from './Timeline.jsx'
import { buildCards, currentStep } from './timeline.js'
import { followRun, startRun } from './api.js'

export default function App() {
  const [rawText, setRawText] = useState(SHOPCART_COMPLAINT)
  const [repoPath, setRepoPath] = useState('fixtures/demo_repos/shopcart')
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const closeStream = useRef(null)
  const bottom = useRef(null)

  // Close the stream if the tab goes away mid-run.
  useEffect(() => () => closeStream.current?.(), [])

  // Follow the newest card as it lands, which is what makes the video readable.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [events.length])

  async function run() {
    closeStream.current?.()
    setEvents([])
    setError(null)
    setStatus('running')
    try {
      const { run_id: runId } = await startRun({ rawText, repoPath })
      closeStream.current = followRun(runId, {
        onEvent: (event) => setEvents((seen) => [...seen, event]),
        onDone: () => setStatus('done'),
        onError: (streamError) => {
          setError(streamError.message)
          setStatus('error')
        },
      })
    } catch (startError) {
      setError(startError.message)
      setStatus('error')
    }
  }

  const cards = buildCards(events)

  return (
    <main className="layout">
      <aside className="col-left">
        <RunForm
          rawText={rawText}
          setRawText={setRawText}
          repoPath={repoPath}
          setRepoPath={setRepoPath}
          onRun={run}
          status={status}
        />
      </aside>
      <section className="col-right">
        <Timeline
          cards={cards}
          step={currentStep(cards, status)}
          status={status}
          error={error}
        />
        <div ref={bottom} />
      </section>
    </main>
  )
}
