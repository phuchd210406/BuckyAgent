import React, { useEffect, useRef, useState } from 'react'
import CodeView from './CodeView.jsx'
import { fetchFile, fetchTree } from './api.js'

/**
 * Read the repository before — and while — the agent works on it.
 *
 * Without this the app offered nine repository names and no way to see a line
 * of what was inside them, which makes a "demo repo" a word rather than a demo:
 * you cannot judge whether the agent found the right file if you have never
 * seen the file. It serves the same checkout a run will use, so a GitHub URL is
 * cloned here — and the run that follows reuses that clone from the cache.
 */

const DEBOUNCE_MS = 700

function sizeOf(bytes) {
  if (bytes < 1024) return `${bytes} B`
  return `${Math.round(bytes / 1024)} KB`
}

function languageOf(path) {
  return path?.endsWith('.py') ? 'python' : 'text'
}

/** A URL worth spending a clone on: `owner/repo`, not half of one being typed. */
function looksComplete(target) {
  if (target.source !== 'github') return Boolean(target.repoPath)
  const value = (target.repoUrl || '').trim()
  return /[^/\s]+\/[^/\s]+/.test(value)
}

export default function RepoBrowser({ target, openPath, onOpened, onUseComplaint }) {
  const [tree, setTree] = useState(null)
  const [selected, setSelected] = useState(null)
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const latest = useRef(0)

  const key = JSON.stringify([target.source, target.repoUrl, target.repoRef, target.repoPath])

  /* Load the file list whenever the repository changes. Debounced, because in
     the GitHub case this is a network clone and the source of the change is
     somebody typing a URL one character at a time. */
  useEffect(() => {
    if (!looksComplete(target)) {
      setTree(null)
      setFile(null)
      setError(null)
      return undefined
    }
    const ticket = (latest.current += 1)
    const timer = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const loaded = await fetchTree(target)
        if (ticket !== latest.current) return // a newer request has overtaken this one
        setTree(loaded)
        setSelected(loaded?.opening_file || null)
      } catch (problem) {
        if (ticket !== latest.current) return
        setTree(null)
        setFile(null)
        setError(problem.message)
      } finally {
        if (ticket === latest.current) setLoading(false)
      }
    }, target.source === 'github' ? DEBOUNCE_MS : 0)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  /* A card asked for a specific file — the localiser's hypothesis, or the test
     an attempt wrote. That is the whole point of having the code on screen. */
  useEffect(() => {
    if (!openPath) return
    setSelected(openPath)
    onOpened?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openPath])

  useEffect(() => {
    if (!selected || !tree) return undefined
    let live = true
    fetchFile(target, selected)
      .then((loaded) => live && setFile(loaded))
      .catch((problem) => live && setFile({ path: selected, text: '', error: problem.message }))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, tree])

  if (!looksComplete(target)) {
    return (
      <div className="panel empty">
        <h2 className="empty-title">No repository chosen</h2>
        <p className="empty-body">
          Paste a GitHub repository, or pick one of the seeded ones, and its code appears
          here — the same checkout the agent will search.
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="panel empty">
        <h2 className="empty-title">Cannot read that repository</h2>
        <p className="empty-body">{error}</p>
      </div>
    )
  }

  if (loading && !tree) {
    return (
      <div className="panel empty">
        <div className="pending">
          <span className="pulse" aria-hidden="true" />
          <span>
            {target.source === 'github' ? 'Cloning the repository' : 'Reading the repository'}…
          </span>
        </div>
      </div>
    )
  }

  if (!tree) return null

  const complaint = tree.case?.complaint

  return (
    <section className="panel browser">
      <header className="browser-head">
        <div>
          <div className="eyebrow">Repository</div>
          <h2 className="card-title">{tree.name}</h2>
        </div>
        <span className="badge badge-neutral">
          {tree.files.length}
          {tree.truncated ? '+' : ''} file{tree.files.length === 1 ? '' : 's'}
        </span>
      </header>

      {complaint && (
        <div className="seeded">
          <div className="seeded-head">
            <span className="eyebrow">The client wrote</span>
            {tree.case.expected_verdict && (
              <span className="badge badge-neutral" title="ground truth from eval/dataset.yaml">
                expected: {tree.case.expected_verdict.replace(/_/g, ' ')}
              </span>
            )}
          </div>
          <blockquote className="seeded-quote">“{complaint}”</blockquote>
          <div className="seeded-actions">
            <button className="inline-action" type="button" onClick={() => onUseComplaint(complaint)}>
              Use this complaint
            </button>
            {tree.case.expected_files?.length > 0 && (
              <span className="seeded-truth">
                the bug is in <code>{tree.case.expected_files.join(', ')}</code>
              </span>
            )}
            {tree.case.expected_files?.length === 0 && (
              <span className="seeded-truth">
                there is nothing here to patch — the agent should say so
              </span>
            )}
          </div>
        </div>
      )}

      <div className="browser-body">
        <ul className="filelist">
          {tree.files.map((entry) => (
            <li key={entry.path}>
              <button
                type="button"
                className={`fileitem ${entry.path === selected ? 'fileitem-on' : ''}`}
                disabled={!entry.readable}
                title={entry.readable ? entry.path : `${entry.path} — not a text file`}
                onClick={() => setSelected(entry.path)}
              >
                <span className="filepath">{entry.path}</span>
                <span className="filemeta">
                  {entry.is_test && <span className="filetag">test</span>}
                  {sizeOf(entry.size)}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="filepane">
          {file?.error ? (
            <p className="note">{file.error}</p>
          ) : file ? (
            <>
              <div className="code-label">
                {file.path} · {file.lines} lines{file.truncated ? ' · truncated' : ''}
              </div>
              <CodeView text={file.text} language={languageOf(file.path)} />
            </>
          ) : (
            <div className="pending">
              <span className="pulse" aria-hidden="true" />
              <span>Opening…</span>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
