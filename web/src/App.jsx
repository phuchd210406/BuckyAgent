import React, { useEffect, useRef, useState } from 'react'
import RunForm from './RunForm.jsx'
import Timeline from './Timeline.jsx'
import CostCounter from './CostCounter.jsx'
import ModelBadge from './ModelBadge.jsx'
import RepoBrowser from './RepoBrowser.jsx'
import { OpenFileContext } from './openFile.js'
import { buildCards, currentStep, latestUsage } from './timeline.js'
import { fetchConfig, followRun, startRun } from './api.js'

export default function App() {
  const [rawText, setRawText] = useState('')
  const [source, setSource] = useState('github') // github | demo
  const [repoUrl, setRepoUrl] = useState('')
  const [repoRef, setRepoRef] = useState('')
  const [repoPath, setRepoPath] = useState('')
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState(null)
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [events, setEvents] = useState([])
  const [failure, setFailure] = useState(null) // {kind, message, runId}
  const [runId, setRunId] = useState(null)
  const [tab, setTab] = useState('code') // code | run
  const [openPath, setOpenPath] = useState(null)
  const closeStream = useRef(null)
  const bottom = useRef(null)

  useEffect(() => () => closeStream.current?.(), [])

  /* Ask the backend what it is before offering to run anything with it. The
     demo repos come from here too — each with the complaint written for it, so
     picking one shows you what you are about to watch. */
  useEffect(() => {
    let live = true
    fetchConfig()
      .then((loaded) => {
        if (!live) return
        setConfig(loaded)
        const first = loaded.demo_repos?.[0]
        if (first) setRepoPath((current) => current || first.path)
      })
      .catch((problem) => live && setConfigError(problem.message))
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    if (tab === 'run') bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [events.length, tab])

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
    setTab('run') // the timeline is the thing to watch from here
    try {
      const { run_id: id } = await startRun({
        rawText,
        repoUrl: source === 'github' ? repoUrl : '',
        repoRef: source === 'github' ? repoRef : '',
        repoPath,
      })
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
    setTab('code')
  }

  /** The run kept going and the server kept the record; ask for it again. */
  function reconnect() {
    if (!runId) return
    setFailure(null)
    setStatus('running')
    follow(runId, { replace: true })
  }

  /* A file path was clicked inside a card — a hypothesis, or the test an
     attempt wrote. Open it in the browser, which is the whole reason the code
     is on screen at all: "it is guessing pricing.py" means nothing until you
     can look at pricing.py. */
  function openFile(path) {
    if (!path) return
    setOpenPath(path)
    setTab('code')
  }

  const cards = buildCards(events)
  const target = { source, repoUrl, repoRef, repoPath }
  const started = status !== 'idle'

  return (
    <OpenFileContext.Provider value={openFile}>
      <main className="layout">
        <CostCounter usage={latestUsage(events)} status={status} />
        <aside className="col-left">
          <ModelBadge config={config} error={configError} />
          <RunForm
            rawText={rawText}
            setRawText={setRawText}
            source={source}
            setSource={setSource}
            repoUrl={repoUrl}
            setRepoUrl={setRepoUrl}
            repoRef={repoRef}
            setRepoRef={setRepoRef}
            repoPath={repoPath}
            setRepoPath={setRepoPath}
            demoRepos={config?.demo_repos || []}
            onRun={run}
            onReset={reset}
            status={status}
            runId={runId}
          />
        </aside>
        <section className="col-right">
          <nav className="tabs" aria-label="what to show on the right">
            <button
              type="button"
              className={tab === 'code' ? 'tab tab-on' : 'tab'}
              onClick={() => setTab('code')}
            >
              Code
            </button>
            <button
              type="button"
              className={tab === 'run' ? 'tab tab-on' : 'tab'}
              onClick={() => setTab('run')}
            >
              Run
              {started && status === 'running' && <span className="tab-dot" aria-hidden="true" />}
            </button>
          </nav>

          {tab === 'code' ? (
            <RepoBrowser
              target={target}
              openPath={openPath}
              onOpened={() => setOpenPath(null)}
              onUseComplaint={setRawText}
            />
          ) : (
            <>
              <Timeline
                cards={cards}
                step={currentStep(cards, status)}
                status={status}
                failure={failure}
                onReconnect={reconnect}
                onRetry={run}
              />
              <div ref={bottom} />
            </>
          )}
        </section>
      </main>
    </OpenFileContext.Provider>
  )
}
