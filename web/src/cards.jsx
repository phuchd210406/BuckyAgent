import React, { useState } from 'react'
import { composeReply, paragraphsOf } from './reply.js'

/* Red and green must survive video compression and colour-blind viewers, so a
   badge never carries meaning in its colour alone: it always has an icon, a
   word, and a border weight. */
function Badge({ tone, icon, children, size = 'normal' }) {
  return (
    <span className={`badge badge-${tone} badge-${size}`}>
      <span className="badge-icon" aria-hidden="true">{icon}</span>
      <span>{children}</span>
    </span>
  )
}
// A value that is null, undefined, or the empty string is rendered as "not stated" in a muted style. Otherwise, the value is rendered as-is. This is
// used for fields that are optional in the intake form.
export function Value({ children }) {
  const empty = children === null || children === undefined || children === ''
  if (empty) return <span className="not-stated">not stated</span>
  return <span>{children}</span>
}

function Field({ label, children }) {
  return (
    <div className="kv">
      <div className="kv-label">{label}</div>
      <div className="kv-value">{children}</div>
    </div>
  )
}

function CodeBlock({ children, label }) {
  return (
    <div className="code">
      {label && <div className="code-label">{label}</div>}
      <pre><code>{children}</code></pre>
    </div>
  )
}

/* A unified diff, coloured by line kind. The +/- gutter carries the same
   information as the colour, so the diff still reads in greyscale. */
function Diff({ text }) {
  const lines = (text || '').replace(/\n$/, '').split('\n')
  return (
    <div className="code diff">
      <div className="code-label">unified diff</div>
      <pre>
        {lines.map((line, index) => {
          let kind = 'ctx'
          if (line.startsWith('+++') || line.startsWith('---')) kind = 'meta'
          else if (line.startsWith('@@')) kind = 'hunk'
          else if (line.startsWith('+')) kind = 'add'
          else if (line.startsWith('-')) kind = 'del'
          return (
            <span key={index} className={`dl dl-${kind}`}>
              {line || ' '}
              {'\n'}
            </span>
          )
        })}
      </pre>
    </div>
  )
}

function Pending({ step }) {
  return (
    <div className="pending">
      <span className="pulse" aria-hidden="true" />
      <span>{step}…</span>
    </div>
  )
}

function Card({ card, eyebrow, title, right, children }) {
  return (
    <li className={`tl-item tl-${card.kind} ${card.state === 'running' ? 'is-running' : ''}`}>
      <span className="tl-dot" aria-hidden="true" />
      <section className="panel card">
        <header className="card-head">
          <div>
            <div className="eyebrow">{eyebrow}</div>
            <h2 className="card-title">{title}</h2>
          </div>
          {right}
        </header>
        {card.state === 'running' ? <Pending step={card.step} /> : children}
      </section>
    </li>
  )
}

/* --- intake ---------------------------------------------------------------- */

function IntakeCard({ card }) {
  const facts = card.data.facts || {}
  const steps = facts.steps || []
  return (
    <Card
      card={card}
      eyebrow="Step 1 · Intake"
      title="What the client told us"
      right={
        card.state === 'done' && (
          <Badge tone="neutral" icon="◑">{Math.round((facts.confidence || 0) * 100)}% confident</Badge>
        )
      }
    >
      <div className="fields">
        <Field label="What happened"><Value>{facts.observed_behaviour}</Value></Field>
        <Field label="What they expected"><Value>{facts.expected_behaviour}</Value></Field>
        <Field label="Steps they described">
          {steps.length ? (
            <ol className="steps">{steps.map((step, i) => <li key={i}>{step}</li>)}</ol>
          ) : (
            <Value>{null}</Value>
          )}
        </Field>
        <Field label="Feature"><Value>{facts.entrypoint_hint}</Value></Field>
        <Field label="Environment"><Value>{facts.environment}</Value></Field>
      </div>
      {(facts.missing || []).length > 0 && (
        <p className="note">
          The report never says: {facts.missing.join(', ')}. Not guessed, and not held
          against the client.
        </p>
      )}
    </Card>
  )
}

/* --- localise -------------------------------------------------------------- */

function LocaliseCard({ card }) {
  const hypotheses = card.data.hypotheses || []
  return (
    <Card
      card={card}
      eyebrow="Step 2 · Localise"
      title="Where the fault probably lives"
      right={
        card.state === 'done' && (
          <Badge tone="neutral" icon="⌕">{hypotheses.length} candidates</Badge>
        )
      }
    >
      <ol className="hyps">
        {hypotheses.map((hypothesis, index) => (
          <li key={index} className="hyp">
            <div className="hyp-head">
              <code className="path">
                {hypothesis.file_path}
                {hypothesis.symbol ? `::${hypothesis.symbol}` : ''}
              </code>
              <span className="conf-pct">{Math.round(hypothesis.confidence * 100)}%</span>
            </div>
            <div className="bar" role="img"
                 aria-label={`confidence ${Math.round(hypothesis.confidence * 100)} percent`}>
              <span className="bar-fill" style={{ width: `${hypothesis.confidence * 100}%` }} />
            </div>
            <p className="hyp-why">{hypothesis.rationale}</p>
          </li>
        ))}
      </ol>
    </Card>
  )
}

/* --- repro ----------------------------------------------------------------- */

function ReproCard({ card }) {
  const data = card.data
  const reproduced = data.reproduced
  const testFailed = (data.failed || 0) > 0 || (data.errors || 0) > 0
  const done = card.state === 'done'

  return (
    <li className={`tl-item tl-repro ${card.state === 'running' ? 'is-running' : ''}`}>
      <span className="tl-dot" aria-hidden="true" />
      <section className={`panel card ${done && !reproduced ? 'card-rejected' : ''}`}>
        <header className="card-head">
          <div>
            <div className="eyebrow">Step 3 · Reproduce · attempt {data.attempt_no} of {data.of || 3}</div>
            <h2 className="card-title">
              {done
                ? reproduced ? 'Reproduced the bug' : 'Did not reproduce the bug'
                : 'Writing a failing test'}
            </h2>
          </div>
          {done && (
            testFailed
              ? <Badge tone="red" icon="✕" size="big">pytest: RED</Badge>
              : <Badge tone="green" icon="✓" size="big">pytest: GREEN</Badge>
          )}
        </header>

        {card.state === 'running' ? (
          <Pending step={card.step} />
        ) : (
          <>
            <p className={`outcome ${reproduced ? 'outcome-yes' : 'outcome-no'}`}>
              <strong>{reproduced ? 'REPRODUCED' : 'NOT A REPRODUCTION'}</strong>
              {' — '}
              {reproduced
                ? 'the test failed, and the failure is the complaint.'
                : 'the test passed, so the reported behaviour did not happen. Trying again.'}
            </p>
            <CodeBlock label={data.test_path}>{data.test_source}</CodeBlock>
            <CodeBlock label={`pytest · ${data.passed || 0} passed, ${data.failed || 0} failed, ${data.errors || 0} errors, ${data.duration_s}s`}>
              {data.stdout_tail}
            </CodeBlock>
            <p className="note">{data.reasoning}</p>
          </>
        )}
      </section>
    </li>
  )
}

/* --- fix ------------------------------------------------------------------- */

function FixCard({ card }) {
  const data = card.data
  const done = card.state === 'done'
  return (
    <li className={`tl-item tl-fix ${card.state === 'running' ? 'is-running' : ''}`}>
      <span className="tl-dot" aria-hidden="true" />
      <section className="panel card card-fix">
        <header className="card-head">
          <div>
            <div className="eyebrow">Step 4 · Fix · attempt {data.attempt_no} of {data.of || 3}</div>
            <h2 className="card-title">{done ? 'Patch proposed and verified twice' : 'Proposing a patch'}</h2>
          </div>
        </header>

        {!done ? (
          <Pending step={card.step} />
        ) : (
          <>
            {/* The safety property, made visual. Nothing else on this screen is
                allowed to be louder than these two. */}
            <div className="verifications">
              <Verification
                label="repro test"
                green={data.target_test_green}
                detail={data.target_test_green ? 'red → green' : 'still red'}
              />
              <Verification
                label="existing suite"
                green={data.suite_green}
                detail={`${data.suite_passed} tests`}
              />
            </div>
            <p className="accepted-line">
              {data.accepted
                ? 'Both green, so the patch is accepted. A patch is never accepted on one of them.'
                : 'Not both green, so the patch is rejected and the workspace is put back.'}
            </p>
            <Diff text={data.unified_diff} />
            <div className="fields">
              <Field label="Files touched">
                {(data.files_touched || []).map((file) => (
                  <code key={file} className="path">{file}</code>
                ))}
              </Field>
              <Field label="Why this fixes it">{data.rationale}</Field>
            </div>
          </>
        )}
      </section>
    </li>
  )
}

function Verification({ label, green, detail }) {
  return (
    <div className={`verify ${green ? 'verify-green' : 'verify-red'}`}>
      <div className="verify-icon" aria-hidden="true">{green ? '✓' : '✕'}</div>
      <div className="verify-text">
        <div className="verify-label">{label}</div>
        <div className="verify-state">{green ? 'GREEN' : 'RED'}</div>
        <div className="verify-detail">{detail}</div>
      </div>
    </div>
  )
}

/* --- verdict --------------------------------------------------------------- */

const VERDICT_COPY = {
  reproduced_and_fixed: { tone: 'green', icon: '✓', text: 'Reproduced and fixed' },
  reproduced_not_fixed: { tone: 'red', icon: '!', text: 'Reproduced, not fixed' },
  not_reproduced: { tone: 'red', icon: '✕', text: 'Not reproduced' },
  needs_clarification: { tone: 'neutral', icon: '?', text: 'Needs clarification' },
  aborted_budget: { tone: 'red', icon: '■', text: 'Stopped: budget' },
}

function VerdictCard({ card }) {
  const data = card.data
  const copy = VERDICT_COPY[data.verdict] || VERDICT_COPY.not_reproduced
  const handover = data.handover || {}
  return (
    <li className="tl-item tl-verdict">
      <span className="tl-dot" aria-hidden="true" />
      <section className="panel card">
        <header className="card-head">
          <div>
            <div className="eyebrow">Step 5 · Handover</div>
            <h2 className="card-title">Two audiences, one run</h2>
          </div>
          <Badge tone={copy.tone} icon={copy.icon} size="big">{copy.text}</Badge>
        </header>

        <article className="pane">
          <h3 className="pane-title">For the developer — PR body</h3>
          <div className="pane-body markdownish">{handover.dev_summary}</div>
        </article>

        <p className="meta-line">
          {data.calls} model calls · ${Number(data.usd || 0).toFixed(4)} · {data.wall_clock_s}s ·
          no auto-merge, a human presses merge
        </p>
      </section>
    </li>
  )
}

/* --- the reply to the client ------------------------------------------------
   Not a developer tool. This is the artefact a support team actually sends, so
   it is laid out like the email it is: sender line, greeting, plain
   paragraphs, no monospace anywhere, and room to breathe. */

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API needs a secure context; a demo served over plain http on a
    // LAN does not have one. Fall back to the old way rather than do nothing.
    try {
      const scratch = document.createElement('textarea')
      scratch.value = text
      scratch.setAttribute('readonly', '')
      scratch.style.position = 'fixed'
      scratch.style.opacity = '0'
      document.body.appendChild(scratch)
      scratch.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(scratch)
      return ok
    } catch {
      return false
    }
  }
}

function ClientReplyCard({ card }) {
  const [copied, setCopied] = useState(false)
  const handover = card.data.handover || {}
  const reply = composeReply(handover.client_reply, card.data.reporter_name)
  const paragraphs = paragraphsOf(reply)
  const recipient = card.data.reporter_name || 'the customer who reported this'

  return (
    <li className="tl-item tl-reply">
      <span className="tl-dot" aria-hidden="true" />
      <section className="reply">
        <header className="reply-head">
          <div className="reply-from">
            <span className="avatar" aria-hidden="true">S</span>
            <div>
              <div className="reply-sender">Support</div>
              <div className="reply-to">to {recipient}</div>
            </div>
          </div>
          <div className="reply-actions">
            <span className="draft-chip">Draft — a person sends it</span>
            <button
              type="button"
              className="copy"
              onClick={async () => {
                if (await copyText(reply)) {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                }
              }}
            >
              {copied ? '✓ Copied' : 'Copy reply'}
            </button>
          </div>
        </header>

        <div className="reply-body">
          {paragraphs.map((paragraph, index) => (
            <p key={index}>{paragraph}</p>
          ))}
        </div>

        <footer className="reply-foot">
          Written for the person who reported the bug, not for the developer who
          fixes it. No jargon, no file names, and it never promises a fix the
          verdict does not support.
        </footer>
      </section>
    </li>
  )
}

function ReportCard({ card }) {
  return <Card card={card} eyebrow="Step 5 · Handover" title="Writing the handover" />
}

function ErrorCard({ card }) {
  return (
    <Card card={card} eyebrow="Error" title="The run stopped"
          right={<Badge tone="red" icon="✕">error</Badge>}>
      <p className="note">{card.data.error || 'unknown error'}</p>
    </Card>
  )
}

const BY_KIND = {
  intake: IntakeCard,
  localise: LocaliseCard,
  repro: ReproCard,
  fix: FixCard,
  report: ReportCard,
  verdict: VerdictCard,
  client_reply: ClientReplyCard,
  error: ErrorCard,
}

export function TimelineCard({ card }) {
  const Component = BY_KIND[card.kind]
  return Component ? <Component card={card} /> : null
}
