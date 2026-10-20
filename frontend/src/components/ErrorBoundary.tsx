import { Button, Result } from 'antd'
import { Component, type ReactNode } from 'react'

/** Contains render errors to one region instead of blanking the whole app. */
export default class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <Result status="warning" title={`${this.props.label || 'This section'} hit an error`} subTitle={this.state.error.message}
        extra={<Button onClick={() => this.setState({ error: null })}>Try again</Button>} />
    )
  }
}
