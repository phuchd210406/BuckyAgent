import React from 'react'

// Mirrors eval/dataset.yaml. Engineer E's remaining cases land here as they do
// there; a path that is not a real checkout would fail at the workspace copy.
const DEMO_REPOS = [
  { path: 'fixtures/demo_repos/shopcart', label: 'shopcart — storefront (Python, pytest)' },
]

// The golden dataset's shopcart complaint, verbatim, misspelling and all.
export const SHOPCART_COMPLAINT =
  'hi, i tried to buy stuff this morning and it charged me postage even though ' +
  'the site says free postage over $50. my basket was definitely more than $50. ' +
  'can you sort it out, we have customers complaining'

export default function RunForm({
  rawText, setRawText, repoPath, setRepoPath, onRun, onReset, status, runId,
}) {
  const running = status === 'running'
  const started = status !== 'idle'

  return (
    <form
      className="panel form"
      onSubmit={(submit) => {
        submit.preventDefault()
        onRun()
      }}
    >
      <h1 className="wordmark">
        Repro<span className="wordmark-dot">.</span>
      </h1>
      <p className="tagline">A client complaint in. A reproduced, verified patch out.</p>

      <label className="field">
        <span className="field-label">Client complaint</span>
        <textarea
          className="complaint"
          rows={7}
          value={rawText}
          disabled={running}
          onChange={(change) => setRawText(change.target.value)}
        />
      </label>

      <label className="field">
        <span className="field-label">Repository</span>
        <select
          className="repo"
          value={repoPath}
          disabled={running}
          onChange={(change) => setRepoPath(change.target.value)}
        >
          {DEMO_REPOS.map((repo) => (
            <option key={repo.path} value={repo.path}>
              {repo.label}
            </option>
          ))}
        </select>
        <span className="field-hint">Copied to a throwaway sandbox. Never written to.</span>
      </label>

      <div className="actions">
        <button className="run" type="submit" disabled={running || !rawText.trim()}>
          {running ? 'Running — watch the timeline' : started ? 'Run again' : 'Run'}
        </button>
        {started && (
          <button
            className="reset"
            type="button"
            onClick={onReset}
            title="Clear the screen for another take. No page reload, no server restart."
          >
            Reset
          </button>
        )}
      </div>
      {runId && <div className="run-id">run {runId.slice(0, 8)}</div>}
    </form>
  )
}
