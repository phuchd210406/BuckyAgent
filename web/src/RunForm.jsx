import React from 'react'

// The golden dataset's shopcart complaint, verbatim, misspelling and all. Kept
// as a one-click sample rather than as the default value of the box: a form
// that arrives pre-filled with the answer is a demo, and this is a tool.
export const SHOPCART_COMPLAINT =
  'hi, i tried to buy stuff this morning and it charged me postage even though ' +
  'the site says free postage over $50. my basket was definitely more than $50. ' +
  'can you sort it out, we have customers complaining'

export default function RunForm({
  rawText, setRawText,
  source, setSource,
  repoUrl, setRepoUrl,
  repoRef, setRepoRef,
  repoPath, setRepoPath,
  demoRepos,
  onRun, onReset, status, runId,
}) {
  const running = status === 'running'
  const started = status !== 'idle'
  const usingGithub = source === 'github'
  const ready = rawText.trim() && (usingGithub ? repoUrl.trim() : repoPath)

  return (
    <form
      className="panel form"
      onSubmit={(submit) => {
        submit.preventDefault()
        if (ready) onRun()
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
          placeholder={
            'Paste what the client actually wrote. Their words, not a rewrite:\n\n' +
            '"the checkout is broken, it charged me for delivery when it said free"'
          }
          onChange={(change) => setRawText(change.target.value)}
        />
        <span className="field-hint">
          Verbatim is better than tidy — the words are what the code search runs on.{' '}
          {!rawText.trim() && (
            <button className="linkish" type="button" onClick={() => setRawText(SHOPCART_COMPLAINT)}>
              use the sample complaint
            </button>
          )}
        </span>
      </label>

      <div className="field">
        <span className="field-label">Repository</span>
        <div className="segmented" role="group" aria-label="where the code lives">
          <button
            type="button"
            className={usingGithub ? 'seg seg-on' : 'seg'}
            disabled={running}
            onClick={() => setSource('github')}
          >
            GitHub
          </button>
          <button
            type="button"
            className={!usingGithub ? 'seg seg-on' : 'seg'}
            disabled={running || demoRepos.length === 0}
            onClick={() => setSource('demo')}
          >
            Demo repo
          </button>
        </div>

        {usingGithub ? (
          <>
            <input
              className="repo"
              type="text"
              value={repoUrl}
              disabled={running}
              spellCheck={false}
              placeholder="https://github.com/owner/repo"
              aria-label="GitHub repository"
              onChange={(change) => setRepoUrl(change.target.value)}
            />
            <input
              className="repo repo-ref"
              type="text"
              value={repoRef}
              disabled={running}
              spellCheck={false}
              placeholder="branch, tag or commit (optional)"
              aria-label="branch, tag or commit"
              onChange={(change) => setRepoRef(change.target.value)}
            />
            <span className="field-hint">
              Cloned shallow, copied to a throwaway sandbox, never written to. Python +
              pytest projects only; a private repo needs GITHUB_TOKEN on the backend.
            </span>
          </>
        ) : (
          <>
            <select
              className="repo"
              value={repoPath}
              disabled={running}
              aria-label="demo repository"
              onChange={(change) => setRepoPath(change.target.value)}
            >
              {demoRepos.map((repo) => (
                <option key={repo.path} value={repo.path}>
                  {repo.name} — seeded bug, stdlib only
                </option>
              ))}
            </select>
            <span className="field-hint">Copied to a throwaway sandbox. Never written to.</span>
          </>
        )}
      </div>

      <div className="actions">
        <button className="run" type="submit" disabled={running || !ready}>
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
