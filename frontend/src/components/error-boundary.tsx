import { Component, type ReactNode } from "react"
import { AlertCircle } from "lucide-react"

interface Props {
  children: ReactNode
  resetKey?: unknown
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = { hasError: false }

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  public componentDidUpdate(prevProps: Props) {
    if (this.props.resetKey !== prevProps.resetKey) {
      this.setState({ hasError: false, error: undefined })
    }
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="p-8 m-4 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive flex flex-col items-start gap-4">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5" />
            <h2 className="font-semibold text-lg">Something went wrong</h2>
          </div>
          <pre className="text-sm font-mono overflow-auto max-w-full p-4 bg-background/50 rounded w-full">
            {this.state.error?.message}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}
