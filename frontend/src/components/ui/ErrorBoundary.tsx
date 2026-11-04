import { Component, type ErrorInfo, type ReactNode } from "react";
import { useRouteError, useNavigate } from "react-router-dom";
import { AlertTriangle, RefreshCw, Home, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  children?: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an unhandled error:", error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="p-6 border border-rose-200 bg-rose-50/70 rounded-xl space-y-4 my-4 max-w-2xl mx-auto shadow-sm">
          <div className="flex items-start gap-3">
            <div className="p-2 bg-rose-100 text-rose-700 rounded-lg shrink-0 mt-0.5">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div className="space-y-1">
              <h3 className="text-sm font-bold text-rose-950">Component Render Warning</h3>
              <p className="text-xs text-rose-800 leading-relaxed">
                {this.state.error?.message || "An unexpected error occurred while rendering this section."}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 pt-2">
            <Button
              size="sm"
              variant="outline"
              className="h-8 text-xs bg-white border-rose-200 text-rose-900 hover:bg-rose-100"
              onClick={() => this.setState({ hasError: false, error: undefined })}
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
              Try Again
            </Button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export function RouteErrorBoundary() {
  const error = useRouteError() as any;
  const navigate = useNavigate();

  const errorMessage =
    error?.message ||
    (typeof error === "string" ? error : "An unexpected application error occurred on this page.");

  return (
    <div className="min-h-[70vh] flex flex-col items-center justify-center p-6 text-center">
      <div className="max-w-md w-full bg-white border border-slate-200 rounded-xl p-6 shadow-sm space-y-4 text-left">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-rose-50 border border-rose-200 text-rose-600 rounded-lg">
            <AlertTriangle className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-slate-900">Application Notice</h2>
            <p className="text-xs text-muted-foreground mt-0.5">The requested page encountered an issue while rendering.</p>
          </div>
        </div>

        <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg font-mono text-xs text-slate-800 break-words leading-relaxed max-h-40 overflow-y-auto">
          {errorMessage}
        </div>

        <div className="flex items-center gap-2 pt-2">
          <Button
            size="sm"
            variant="default"
            className="h-8 text-xs flex-1 bg-blue-600 hover:bg-blue-700"
            onClick={() => window.location.reload()}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            Reload Page
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="h-8 text-xs flex-1"
            onClick={() => navigate("/execution-history")}
          >
            <ArrowLeft className="w-3.5 h-3.5 mr-1.5" />
            History
          </Button>

          <Button
            size="sm"
            variant="ghost"
            className="h-8 text-xs px-2"
            onClick={() => navigate("/dashboard")}
            title="Dashboard"
          >
            <Home className="w-4 h-4 text-slate-600" />
          </Button>
        </div>
      </div>
    </div>
  );
}
