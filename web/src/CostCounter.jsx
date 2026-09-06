import React from 'react'

/** The running bill, in the corner, all run long. "$0.019 so far" on screen
 *  during the video is the whole cost argument made without a slide. */
export default function CostCounter({ usage, status }) {
  if (!usage) return null
  const { usd = 0, input_tokens: input = 0, output_tokens: output = 0, calls = 0 } = usage
  const tokens = input + output

  return (
    <aside
      className={`cost ${status === 'running' ? 'cost-live' : ''}`}
      aria-live="polite"
      aria-label="cost of this run"
    >
      <div className="cost-label">
        {status === 'running' ? 'this run, so far' : 'this run cost'}
      </div>
      <div className="cost-usd">${usd.toFixed(4)}</div>
      <div className="cost-detail">
        {tokens.toLocaleString()} tokens · {calls} call{calls === 1 ? '' : 's'}
      </div>
      <div className="cost-split">
        {input.toLocaleString()} in · {output.toLocaleString()} out
      </div>
    </aside>
  )
}
