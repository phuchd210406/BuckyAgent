import React from 'react'

/** A render crash must not become a white page in front of a judge. */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('the UI crashed while rendering', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="crash">
        <h1 className="crash-title">The page hit an error</h1>
        <p className="crash-body">
          The run itself is unaffected — it is recorded on the server, and reloading
          shows it. This is a bug in the display only.
        </p>
        <pre className="crash-detail">{String(this.state.error)}</pre>
        <button className="run" type="button" onClick={() => window.location.reload()}>
          Reload the page
        </button>
      </div>
    )
  }
}
