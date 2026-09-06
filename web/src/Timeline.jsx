import React from 'react'
import { TimelineCard } from './cards.jsx'

/* A failure is not a crash and must not read like one. Each of these says what
   happened, what survived it, and what to press. */
function Failure({ failure, onReconnect, onRetry }) {
  if (failure.kind === 'connection_lost') {
    return (
      <div className="stepbar stepbar-warn" role="status">
        <span className="warn-icon" aria-hidden="true">⚠</span>
        <span className="failure-text">
          <strong>Connection lost.</strong> The run is still going on the server and its
          record is kept — reconnecting replays it from the beginning.
        </span>
        <button className="inline-action" type="button" onClick={onReconnect}>
          Reconnect
        </button>
      </div>
    )
  }
  if (failure.kind === 'unreachable') {
    return (
      <div className="stepbar stepbar-warn" role="status">
        <span className="warn-icon" aria-hidden="true">⚠</span>
        <span className="failure-text">
          <strong>Cannot reach the backend.</strong> Nothing has started, so nothing was
          lost. Check the API is up, then try again.
          <span className="failure-detail">{failure.message}</span>
        </span>
        <button className="inline-action" type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    )
  }
  return (
    <div className="stepbar stepbar-error" role="status">
      <span className="cross" aria-hidden="true">✕</span>
      <span className="failure-text">
        <strong>The run stopped.</strong> Everything it finished before stopping is below.
        <span className="failure-detail">{failure.message}</span>
      </span>
    </div>
  )
}

export default function Timeline({ cards, step, status, failure, onReconnect, onRetry }) {
  if (status === 'idle') {
    return (
      <div className="panel empty">
        <h2 className="empty-title">Nothing running</h2>
        <p className="empty-body">
          Press <strong>Run</strong>. The timeline fills in as the agent works: what it
          understood, where it looked, every test it wrote — including the ones that did
          not reproduce the bug — and the patch, only once two suites are green.
        </p>
      </div>
    )
  }

  return (
    <>
      {status === 'error' && failure ? (
        <Failure failure={failure} onReconnect={onReconnect} onRetry={onRetry} />
      ) : (
        <div className={`stepbar ${status === 'done' ? 'stepbar-done' : ''}`} aria-live="polite">
          {status === 'done' ? (
            <><span className="tick" aria-hidden="true">✓</span> Run finished</>
          ) : (
            <><span className="pulse" aria-hidden="true" /> {step}…</>
          )}
        </div>
      )}
      <ol className="timeline">
        {cards.map((card) => <TimelineCard key={card.key} card={card} />)}
      </ol>
    </>
  )
}
