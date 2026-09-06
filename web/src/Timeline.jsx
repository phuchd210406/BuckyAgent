import React from 'react'
import { TimelineCard } from './cards.jsx'

export default function Timeline({ cards, step, status, error }) {
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
      <div className={`stepbar ${status === 'done' ? 'stepbar-done' : ''}`} aria-live="polite">
        {status === 'done' ? (
          <><span className="tick" aria-hidden="true">✓</span> Run finished</>
        ) : status === 'error' ? (
          <><span className="cross" aria-hidden="true">✕</span> {error}</>
        ) : (
          <><span className="pulse" aria-hidden="true" /> {step}…</>
        )}
      </div>
      <ol className="timeline">
        {cards.map((card) => <TimelineCard key={card.key} card={card} />)}
      </ol>
    </>
  )
}
