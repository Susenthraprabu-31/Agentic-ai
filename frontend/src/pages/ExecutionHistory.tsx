import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  History,
  Loader2,
  RefreshCw,
  Search,
  StopCircle,
  XCircle,
} from "lucide-react";
import { executionsApi } from "@/api/executions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { WorkflowRun } from "@/types";

function runStatusBadge(status: string) {
  const s = status.toUpperCase();
  switch (s) {
    case "COMPLETED":
      return <Badge className="bg-emerald-600 hover:bg-emerald-700 text-white">Completed</Badge>;
    case "PARTIALLY_COMPLETED":
      return <Badge className="bg-purple-600 hover:bg-purple-700 text-white">Partial Success</Badge>;
    case "RUNNING":
      return (
        <Badge className="bg-blue-600 hover:bg-blue-700 text-white flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin" />
          Running
        </Badge>
      );
    case "QUEUED":
    case "PENDING":
      return <Badge className="bg-amber-500 hover:bg-amber-600 text-white">Queued</Badge>;
    case "CANCEL_REQUESTED":
      return <Badge className="bg-slate-500 text-white">Cancelling...</Badge>;
    case "CANCELLED":
      return <Badge variant="secondary">Cancelled</Badge>;
    case "FAILED":
      return <Badge variant="destructive">Failed</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export default function ExecutionHistory() {
  const navigate = useNavigate();

  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  const fetchRuns = async () => {
    try {
      setLoading(true);
      const res = await executionsApi.list({
        status: statusFilter === "ALL" ? undefined : statusFilter,
        limit: 100,
      });
      setRuns(res.items || []);
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to load execution runs.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuns();
  }, [statusFilter]);

  const handleCancelRun = async (runId: string) => {
    if (!confirm("Request cancellation for this workflow run?")) return;
    try {
      setCancellingId(runId);
      await executionsApi.cancel(runId);
      fetchRuns();
    } catch (err: any) {
      setErrorMsg(err.response?.data?.detail || "Failed to cancel run.");
    } finally {
      setCancellingId(null);
    }
  };

  const filteredRuns = runs.filter((r) => {
    if (!search) return true;
    const q = search.toLowerCase();
    const idMatch = r.id.toLowerCase().includes(q);
    const nameMatch = (r.workflow_name || "").toLowerCase().includes(q);
    const parcelMatch = JSON.stringify(r.input_data || {}).toLowerCase().includes(q);
    return idMatch || nameMatch || parcelMatch;
  });

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">
            Execution History
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Monitor Celery background executions, branch outcomes, fault tolerance, and duration.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={fetchRuns} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Global Alerts */}
      {errorMsg && (
        <div className="p-3 text-sm bg-rose-50 border border-rose-200 text-rose-800 rounded-md flex items-center justify-between">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-rose-600 shrink-0" />
            <span>{errorMsg}</span>
          </div>
          <button onClick={() => setErrorMsg(null)} className="text-rose-500 hover:text-rose-700">
            ×
          </button>
        </div>
      )}

      {/* Filters Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 border rounded-lg shadow-sm">
        <div className="relative w-full sm:w-80">
          <Search className="absolute left-2.5 top-2.5 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search by Run ID, workflow, or parcel..."
            className="pl-8 text-xs"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <select
            className="text-xs border rounded-md px-3 py-2 bg-white text-slate-700 border-slate-300 h-9"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="ALL">All Statuses</option>
            <option value="COMPLETED">Completed</option>
            <option value="PARTIALLY_COMPLETED">Partially Completed</option>
            <option value="RUNNING">Running</option>
            <option value="QUEUED">Queued</option>
            <option value="FAILED">Failed</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
      </div>

      {/* Executions Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Execution ID</TableHead>
                <TableHead>Workflow & Version</TableHead>
                <TableHead>Search Input</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead>Started</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center py-12">
                    <Loader2 className="w-6 h-6 animate-spin mx-auto text-blue-600" />
                  </TableCell>
                </TableRow>
              ) : filteredRuns.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7}>
                    <div className="flex flex-col items-center justify-center py-14 text-center text-muted-foreground">
                      <History className="w-10 h-10 mb-3 opacity-25" />
                      <p className="font-medium text-sm">No execution records found</p>
                      <p className="text-xs mt-1">
                        Run a workflow from the Workflow Builder to generate execution records.
                      </p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                filteredRuns.map((run) => {
                  const isTerminal = ["COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"].includes(
                    run.status.toUpperCase()
                  );
                  const parcelId = run.input_data?.parcel_id as string | undefined;

                  return (
                    <TableRow key={run.id}>
                      <TableCell className="font-mono text-xs font-semibold text-slate-800">
                        {run.id.slice(0, 8)}...
                      </TableCell>
                      <TableCell>
                        <div className="font-medium text-slate-900 text-xs">
                          {run.workflow_name || "Workflow"}
                        </div>
                        <div className="text-[10px] text-muted-foreground">
                          Version {run.version_number || 1}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs font-mono text-slate-600">
                        {parcelId ? `Parcel: ${parcelId}` : "—"}
                      </TableCell>
                      <TableCell>{runStatusBadge(run.status)}</TableCell>
                      <TableCell className="text-xs font-mono">
                        {formatDuration(run.duration_ms)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {run.started_at
                          ? new Date(run.started_at).toLocaleString()
                          : new Date(run.created_at).toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {!isTerminal && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleCancelRun(run.id)}
                              disabled={cancellingId === run.id}
                              className="text-xs h-7 text-rose-600 border-rose-200 hover:bg-rose-50"
                            >
                              <StopCircle className="w-3 h-3 mr-1" />
                              Cancel
                            </Button>
                          )}
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => navigate(`/execution-results?id=${run.id}`)}
                            className="text-xs h-7 flex items-center gap-1"
                          >
                            View Results
                            <ArrowRight className="w-3 h-3" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
