import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * App-level error boundary. A single render throw (e.g. a malformed row
 * blowing up `new URL(...)`) used to white-screen the whole console. This
 * catches it and offers a recovery path instead.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep a console trail for debugging; no telemetry sink in the client.
    console.error("Unhandled UI error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-background p-6">
          <div className="max-w-md text-center border border-dashed rounded-xl bg-subtle/60 px-8 py-12">
            <div className="rounded-full bg-destructive/10 ring-1 ring-destructive/20 p-3 mb-4 inline-flex">
              <AlertTriangle className="h-6 w-6 text-destructive" aria-hidden />
            </div>
            <h1 className="font-serif text-lg font-semibold mb-1.5 tracking-tight">
              Something broke on this screen
            </h1>
            <p className="text-sm text-muted-foreground leading-relaxed mb-5">
              The page hit an unexpected error and couldn't finish rendering.
              Reloading usually clears it.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="inline-flex items-center justify-center h-10 px-4 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
