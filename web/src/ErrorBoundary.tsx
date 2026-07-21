import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

// A render error anywhere below an unguarded React root unmounts the WHOLE tree —
// the user gets a blank white page with no way back (this is what React error #310,
// the hook-after-early-return bug in LibraryPrefs, did to the app). These boundaries
// contain the damage and always leave a way out.
//
// Use `scope="root"` once at the top; `scope="panel"` around any independently
// swappable chunk of UI (a settings panel, an overlay body) so one broken panel
// can't take the app with it.

type Props = {
  children: ReactNode
  /** Shown in the message so the user knows what died. e.g. "the Library settings". */
  label?: string
  /** root = full-screen fallback; panel = inline, keeps the surrounding chrome usable. */
  scope?: 'root' | 'panel'
}

type State = { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the real stack in the console — the fallback deliberately shows only a
    // short message, but a bug report needs the component stack.
    console.error('[ludodex] render error', error, info.componentStack)
  }

  private reset = () => this.setState({ error: null })

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    const { label, scope = 'panel' } = this.props
    const what = label ? `${label} ran into an error` : 'Something went wrong'

    return (
      <div className={'errbound' + (scope === 'root' ? ' errbound-root' : '')} role="alert">
        <div className="errbound-title">{what}</div>
        <div className="errbound-msg">{error.message || String(error)}</div>
        <div className="errbound-actions">
          <button className="errbound-btn" onClick={this.reset}>Try again</button>
          {scope === 'root' && (
            <button className="errbound-btn" onClick={() => location.reload()}>Reload ludodex</button>
          )}
        </div>
        <div className="errbound-hint">
          The full stack trace is in your browser console (F12).
        </div>
      </div>
    )
  }
}
