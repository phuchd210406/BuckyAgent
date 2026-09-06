import React from 'react'

/**
 * What the backend is actually going to do, said out loud.
 *
 * The failure this exists to prevent: the page looks identical whether Claude is
 * being called or whether recorded replies are being replayed, so a run against
 * a repository nobody ever recorded looks like a model that has gone stupid
 * rather than like a missing credential. When there is no live model this says
 * so, and says which environment variable fixes it.
 */
export default function ModelBadge({ config, error }) {
  if (error) {
    return (
      <div className="modelbar modelbar-warn">
        <strong>Backend unreachable.</strong> Start it with <code>make api</code> — the page
        cannot run anything without it.
      </div>
    )
  }
  if (!config) return null

  if (config.mode === 'mock') {
    return (
      <div className="modelbar modelbar-warn">
        <strong>Rehearsal mode (MOCK=1).</strong> A recorded run is being replayed on a timer.
        Nothing is cloned, no tests run, no model is called.
      </div>
    )
  }

  if (!config.live_model) {
    return (
      <div className="modelbar modelbar-warn">
        <strong>No model credentials.</strong> Runs will replay recorded answers, which only
        exist for the demo repo. Set <code>ANTHROPIC_API_KEY</code> (or refresh the AWS keys
        and set <code>LLM_PROVIDER=bedrock</code>) and restart <code>make api</code>.
      </div>
    )
  }

  return (
    <div className="modelbar">
      <span className="modeldot" aria-hidden="true" />
      <strong>{config.model}</strong> is answering. Hard stop at $
      {Number(config.max_run_usd).toFixed(2)} a run
      {config.install_deps !== 'never' && ', dependencies installed per run'}.
    </div>
  )
}
