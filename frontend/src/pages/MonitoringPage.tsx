import { useEffect, useState, useCallback } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  History,
  Loader2,
  RefreshCw,
  RotateCcw,
  Server,
  ShieldAlert,
  Zap,
} from "lucide-react";
import { monitoringApi } from "@/api/monitoring";
import { executionsApi } from "@/api/executions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  CircuitBreakerState,
  RecoveryAuditRecord,
  WorkerHealthResponse,
  WorkflowRun,
} from "@/types";

export default function MonitoringPage() {
  const [activeTab, setActiveTab] = useState<"workers" | "circuits" | "recovery">("workers");
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Data states
  const [workerHealth, setWorkerHealth] = useState<WorkerHealthResponse | null>(null);
  const [circuits, setCircuits] = useState<CircuitBreakerState[]>([]);
  const [interruptedRuns, setInterruptedRuns] = useState<WorkflowRun[]>([]);

  // Search & filter states
  const [workerSearch, setWorkerSearch] = useState("");
  const [circuitSearch, setCircuitSearch] = useState("");
  const [circuitFilter, setCircuitFilter] = useState<string>("ALL");

  // Reset circuit modal state
  const [resettingCircuit, setResettingCircuit] = useState<CircuitBreakerState | null>(null);
  const [isResetting, setIsResetting] = useState(false);

  // Recovery modal state
  const [recoveringRun, setRecoveringRun] = useState<WorkflowRun | null>(null);
  const [isTriggeringRecovery, setIsTriggeringRecovery] = useState(false);

  // Recovery audit log viewer
  const [auditRunId, setAuditRunId] = useState<string | null>(null);
  const [auditLogs, setAuditLogs] = useState<RecoveryAuditRecord[]>([]);
  const [loadingAudit, setLoadingAudit] = useState(false);

  // Fetch all monitoring data
  const fetchData = useCallback(async () => {
    try {
      const [healthRes, circuitsRes, runsRes] = await Promise.all([
        monitoringApi.getWorkerHealth(),
        monitoringApi.getCircuitBreakers(),
        executionsApi.list({ limit: 50 }),
      ]);

      setWorkerHealth(healthRes || null);
      setCircuits(Array.isArray(circuitsRes) ? circuitsRes : []);

      // Filter runs that are recovering or have recovery history
      const runItems = Array.isArray(runsRes?.items) ? runsRes.items : (Array.isArray(runsRes) ? (runsRes as any) : []);
      const recoveringOrStalled = runItems.filter(
        (r: any) =>
          (r?.status && r.status.toUpperCase() === "RECOVERING") ||
          (r?.recovery_attempt_count && r.recovery_attempt_count > 0) ||
          (r?.status && r.status.toUpperCase() === "RUNNING")
      );
      setInterruptedRuns(recoveringOrStalled);
      setErrorMsg(null);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load monitoring data";
      setErrorMsg(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Periodic polling if auto-refresh is on
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchData();
    }, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchData]);

  // Handle manual circuit reset
  const handleConfirmResetCircuit = async () => {
    if (!resettingCircuit) return;
    setIsResetting(true);
    try {
      await monitoringApi.resetCircuitBreaker(resettingCircuit.dependency_key);
      setSuccessMsg(`Circuit for '${resettingCircuit.dependency_key}' reset to CLOSED.`);
      setResettingCircuit(null);
      await fetchData();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to reset circuit breaker");
    } finally {
      setIsResetting(false);
    }
  };

  // Handle manual execution recovery
  const handleConfirmRecoverRun = async () => {
    if (!recoveringRun) return;
    setIsTriggeringRecovery(true);
    try {
      const res = await monitoringApi.triggerManualRecovery(recoveringRun.id);
      setSuccessMsg(`Recovery initiated: ${res.message}`);
      setRecoveringRun(null);
      await fetchData();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to trigger workflow recovery");
    } finally {
      setIsTriggeringRecovery(false);
    }
  };

  // View audit history
  const handleViewAuditLogs = async (runId: string) => {
    setAuditRunId(runId);
    setLoadingAudit(true);
    try {
      const logs = await monitoringApi.getExecutionRecovery(runId);
      setAuditLogs(logs);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to load recovery audit history");
    } finally {
      setLoadingAudit(false);
    }
  };

  // Helpers
  const workerList = Array.isArray(workerHealth?.workers) ? workerHealth.workers : [];
  const filteredWorkers = workerList.filter(
    (w) =>
      w?.worker_id?.toLowerCase().includes(workerSearch.toLowerCase()) ||
      (w?.hostname && w.hostname.toLowerCase().includes(workerSearch.toLowerCase()))
  );

  const circuitList = Array.isArray(circuits) ? circuits : [];
  const filteredCircuits = circuitList.filter((c) => {
    const matchesSearch =
      c?.dependency_key?.toLowerCase().includes(circuitSearch.toLowerCase()) ||
      c?.dependency_type?.toLowerCase().includes(circuitSearch.toLowerCase());
    const matchesFilter = circuitFilter === "ALL" || (c?.state && c.state.toUpperCase() === circuitFilter);
    return matchesSearch && matchesFilter;
  });

  const openCircuitsCount = circuitList.filter((c) => c?.state?.toUpperCase() === "OPEN").length;
  const halfOpenCircuitsCount = circuitList.filter((c) => c?.state?.toUpperCase() === "HALF_OPEN").length;

  return (
    <div className="space-y-6">
      {/* Top Header & Actions */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 flex items-center gap-2">
            <Activity className="w-6 h-6 text-indigo-600" />
            Worker & Resilience Monitoring
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Real-time worker heartbeats, dependency circuit breakers, and interrupted workflow recovery.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={autoRefresh ? "default" : "outline"}
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
            className="flex items-center gap-1.5"
          >
            <Activity className="w-4 h-4" />
            Auto-refresh: {autoRefresh ? "ON (10s)" : "OFF"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchData}
            disabled={loading}
            className="flex items-center gap-1.5"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Notifications */}
      {errorMsg && (
        <div className="p-4 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            <span>{errorMsg}</span>
          </div>
          <button onClick={() => setErrorMsg(null)} className="text-red-500 hover:text-red-700 font-bold ml-4">
            ×
          </button>
        </div>
      )}
      {successMsg && (
        <div className="p-4 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
            <span>{successMsg}</span>
          </div>
          <button onClick={() => setSuccessMsg(null)} className="text-emerald-500 hover:text-emerald-700 font-bold ml-4">
            ×
          </button>
        </div>
      )}

      {/* KPI Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Cluster Health */}
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
              Worker Cluster
              <Server className="w-4 h-4 text-slate-400" />
            </CardDescription>
            <CardTitle className="text-2xl font-bold flex items-center gap-2">
              {workerHealth?.overall_status === "HEALTHY" ? (
                <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white">HEALTHY</Badge>
              ) : workerHealth?.overall_status === "DEGRADED" ? (
                <Badge className="bg-amber-500 hover:bg-amber-500 text-white">DEGRADED</Badge>
              ) : (
                <Badge variant="destructive">UNHEALTHY</Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-xs text-slate-500 flex justify-between">
              <span>{workerHealth?.online_workers ?? 0} online / {workerHealth?.total_workers ?? 0} total</span>
              {workerHealth?.unresponsive_workers ? (
                <span className="text-red-600 font-semibold">{workerHealth.unresponsive_workers} unresponsive</span>
              ) : null}
            </div>
          </CardContent>
        </Card>

        {/* Active Worker Tasks */}
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
              Active Tasks
              <Cpu className="w-4 h-4 text-slate-400" />
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-slate-900">
              {workerHealth?.active_tasks ?? 0}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-xs text-slate-500">
              Across {workerHealth?.busy_workers ?? 0} currently busy workers
            </div>
          </CardContent>
        </Card>

        {/* Circuit Breakers Tripped */}
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
              Circuit Breakers
              <ShieldAlert className="w-4 h-4 text-slate-400" />
            </CardDescription>
            <CardTitle className="text-2xl font-bold flex items-center gap-2">
              {openCircuitsCount > 0 ? (
                <span className="text-red-600 flex items-center gap-1.5">
                  <AlertTriangle className="w-5 h-5 text-red-500" />
                  {openCircuitsCount} OPEN
                </span>
              ) : halfOpenCircuitsCount > 0 ? (
                <span className="text-amber-600">{halfOpenCircuitsCount} HALF-OPEN</span>
              ) : (
                <span className="text-emerald-600">All Healthy</span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-xs text-slate-500">
              {circuits.length} monitored external dependencies
            </div>
          </CardContent>
        </Card>

        {/* Recovered Executions */}
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-2">
            <CardDescription className="text-xs font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
              Workflow Resilience
              <RotateCcw className="w-4 h-4 text-slate-400" />
            </CardDescription>
            <CardTitle className="text-2xl font-bold text-slate-900">
              {interruptedRuns.filter((r) => r.recovery_attempt_count && r.recovery_attempt_count > 0).length}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-xs text-slate-500">
              Recovered runs across cluster outages
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Tabs Navigation */}
      <div className="border-b border-slate-200">
        <nav className="flex space-x-8" aria-label="Tabs">
          <button
            onClick={() => setActiveTab("workers")}
            className={`py-3 px-1 border-b-2 font-medium text-sm flex items-center gap-2 ${
              activeTab === "workers"
                ? "border-indigo-600 text-indigo-600"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
            }`}
          >
            <Server className="w-4 h-4" />
            Workers & Heartbeats
            <Badge variant="secondary" className="ml-1 text-xs">
              {workerHealth?.workers?.length ?? workerHealth?.total_workers ?? 0}
            </Badge>
          </button>

          <button
            onClick={() => setActiveTab("circuits")}
            className={`py-3 px-1 border-b-2 font-medium text-sm flex items-center gap-2 ${
              activeTab === "circuits"
                ? "border-indigo-600 text-indigo-600"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
            }`}
          >
            <Zap className="w-4 h-4" />
            Circuit Breakers
            {openCircuitsCount > 0 ? (
              <Badge variant="destructive" className="ml-1 text-xs">
                {openCircuitsCount} OPEN
              </Badge>
            ) : (
              <Badge variant="secondary" className="ml-1 text-xs">
                {circuits.length}
              </Badge>
            )}
          </button>

          <button
            onClick={() => setActiveTab("recovery")}
            className={`py-3 px-1 border-b-2 font-medium text-sm flex items-center gap-2 ${
              activeTab === "recovery"
                ? "border-indigo-600 text-indigo-600"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
            }`}
          >
            <History className="w-4 h-4" />
            Workflow Recovery & Fencing
            <Badge variant="secondary" className="ml-1 text-xs">
              {interruptedRuns.length}
            </Badge>
          </button>
        </nav>
      </div>

      {/* TAB 1: WORKERS MONITORING */}
      {activeTab === "workers" && (
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-3 flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base font-semibold">Registered Celery Workers</CardTitle>
              <CardDescription className="text-xs">
                Automated worker heartbeats report health every 15s. Workers missing heartbeats for &gt;60s are flagged unresponsive.
              </CardDescription>
            </div>
            <div className="w-64">
              <Input
                placeholder="Search workers or host..."
                value={workerSearch}
                onChange={(e) => setWorkerSearch(e.target.value)}
                className="h-8 text-xs"
              />
            </div>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border border-slate-200 overflow-hidden">
              <Table>
                <TableHeader className="bg-slate-50">
                  <TableRow>
                    <TableHead className="font-semibold text-xs">Worker ID</TableHead>
                    <TableHead className="font-semibold text-xs">Host & PID</TableHead>
                    <TableHead className="font-semibold text-xs">Status</TableHead>
                    <TableHead className="font-semibold text-xs">Active Tasks</TableHead>
                    <TableHead className="font-semibold text-xs">Heartbeat Age</TableHead>
                    <TableHead className="font-semibold text-xs">Assigned Queues</TableHead>
                    <TableHead className="font-semibold text-xs">Registered At</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredWorkers.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-8 text-slate-400 text-sm">
                        No registered workers found.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredWorkers.map((w) => {
                      const st = w.status.toUpperCase();
                      return (
                        <TableRow key={w.worker_id} className="hover:bg-slate-50/50">
                          <TableCell className="font-mono text-xs font-semibold text-slate-900">
                            {w.worker_id}
                          </TableCell>
                          <TableCell className="text-xs text-slate-600">
                            {w.hostname ?? "—"} {w.pid ? `(PID: ${w.pid})` : ""}
                          </TableCell>
                          <TableCell>
                            {st === "ONLINE" && (
                              <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white text-xs">
                                ONLINE
                              </Badge>
                            )}
                            {st === "BUSY" && (
                              <Badge className="bg-blue-600 hover:bg-blue-600 text-white text-xs flex items-center gap-1 w-fit">
                                <Loader2 className="w-3 h-3 animate-spin" />
                                BUSY
                              </Badge>
                            )}
                            {st === "UNRESPONSIVE" && (
                              <Badge variant="destructive" className="text-xs">
                                UNRESPONSIVE
                              </Badge>
                            )}
                            {st === "OFFLINE" && (
                              <Badge variant="secondary" className="text-xs">
                                OFFLINE
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-xs font-semibold text-slate-800">
                            {w.active_task_count} {w.current_task_id ? `(${w.current_task_id})` : ""}
                          </TableCell>
                          <TableCell className="text-xs text-slate-600">
                            {w.heartbeat_age_seconds !== null
                              ? `${w.heartbeat_age_seconds}s ago`
                              : "—"}
                          </TableCell>
                          <TableCell className="text-xs text-slate-500">
                            {w.queues.join(", ") || "default"}
                          </TableCell>
                          <TableCell className="text-xs text-slate-500">
                            {w.registered_at
                              ? new Date(w.registered_at).toLocaleString()
                              : "—"}
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* TAB 2: CIRCUIT BREAKERS */}
      {activeTab === "circuits" && (
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-3 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <CardTitle className="text-base font-semibold">External Dependency Circuit Breakers</CardTitle>
              <CardDescription className="text-xs">
                Tripped circuits block repetitive calls to failing county portals or services for a 60s cooldown before attempting HALF_OPEN probes.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={circuitFilter}
                onChange={(e) => setCircuitFilter(e.target.value)}
                className="h-8 text-xs border border-slate-300 rounded px-2 bg-white text-slate-700 focus:outline-none"
              >
                <option value="ALL">All States</option>
                <option value="CLOSED">CLOSED (Healthy)</option>
                <option value="OPEN">OPEN (Tripped)</option>
                <option value="HALF_OPEN">HALF_OPEN (Probing)</option>
              </select>
              <Input
                placeholder="Search dependencies..."
                value={circuitSearch}
                onChange={(e) => setCircuitSearch(e.target.value)}
                className="h-8 text-xs w-56"
              />
            </div>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border border-slate-200 overflow-hidden">
              <Table>
                <TableHeader className="bg-slate-50">
                  <TableRow>
                    <TableHead className="font-semibold text-xs">Dependency Key</TableHead>
                    <TableHead className="font-semibold text-xs">Type</TableHead>
                    <TableHead className="font-semibold text-xs">State</TableHead>
                    <TableHead className="font-semibold text-xs">Failures / Limit</TableHead>
                    <TableHead className="font-semibold text-xs">Successes</TableHead>
                    <TableHead className="font-semibold text-xs">Cooldown Expiration</TableHead>
                    <TableHead className="font-semibold text-xs">Last Failure Reason</TableHead>
                    <TableHead className="font-semibold text-xs text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredCircuits.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center py-8 text-slate-400 text-sm">
                        No circuit breakers found.
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredCircuits.map((cb) => {
                      const st = cb.state.toUpperCase();
                      return (
                        <TableRow key={cb.dependency_key} className="hover:bg-slate-50/50">
                          <TableCell className="font-mono text-xs font-semibold text-slate-900">
                            {cb.dependency_key}
                          </TableCell>
                          <TableCell className="text-xs text-slate-600 capitalize">
                            {cb.dependency_type.replace("_", " ")}
                          </TableCell>
                          <TableCell>
                            {st === "CLOSED" && (
                              <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white text-xs">
                                CLOSED
                              </Badge>
                            )}
                            {st === "OPEN" && (
                              <Badge variant="destructive" className="text-xs flex items-center gap-1 w-fit">
                                <AlertTriangle className="w-3 h-3" />
                                OPEN
                              </Badge>
                            )}
                            {st === "HALF_OPEN" && (
                              <Badge className="bg-amber-500 hover:bg-amber-500 text-white text-xs">
                                HALF_OPEN
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-xs text-slate-700">
                            <span className={cb.failure_count > 0 ? "font-semibold text-red-600" : ""}>
                              {cb.failure_count}
                            </span>{" "}
                            / {cb.failure_threshold}
                          </TableCell>
                          <TableCell className="text-xs text-slate-700">
                            {cb.success_count} ({cb.consecutive_successes} consecutive)
                          </TableCell>
                          <TableCell className="text-xs text-slate-500">
                            {cb.open_until ? new Date(cb.open_until).toLocaleTimeString() : "—"}
                          </TableCell>
                          <TableCell className="text-xs text-slate-500 max-w-xs truncate" title={cb.last_failure_reason ?? ""}>
                            {cb.last_failure_reason || "—"}
                          </TableCell>
                          <TableCell className="text-right">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs text-slate-700 hover:text-slate-900"
                              onClick={() => setResettingCircuit(cb)}
                              disabled={st === "CLOSED"}
                            >
                              Reset
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* TAB 3: WORKFLOW RECOVERY & FENCING */}
      {activeTab === "recovery" && (
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-3 flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base font-semibold">Active & Interrupted Workflow Recovery</CardTitle>
              <CardDescription className="text-xs">
                Executions interrupted by worker crashes are automatically claimed by healthy workers. Monotonically increasing fencing tokens safeguard against stale zombie worker writes.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border border-slate-200 overflow-hidden">
              <Table>
                <TableHeader className="bg-slate-50">
                  <TableRow>
                    <TableHead className="font-semibold text-xs">Run ID</TableHead>
                    <TableHead className="font-semibold text-xs">Workflow</TableHead>
                    <TableHead className="font-semibold text-xs">Status</TableHead>
                    <TableHead className="font-semibold text-xs">Fencing Token</TableHead>
                    <TableHead className="font-semibold text-xs">Assigned Worker</TableHead>
                    <TableHead className="font-semibold text-xs">Recovery Attempts</TableHead>
                    <TableHead className="font-semibold text-xs">Recovery Reason</TableHead>
                    <TableHead className="font-semibold text-xs text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {interruptedRuns.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center py-8 text-slate-400 text-sm">
                        No interrupted or recovering executions currently tracked.
                      </TableCell>
                    </TableRow>
                  ) : (
                    interruptedRuns.map((run) => {
                      const st = run.status.toUpperCase();
                      return (
                        <TableRow key={run.id} className="hover:bg-slate-50/50">
                          <TableCell className="font-mono text-xs font-semibold text-slate-900">
                            {run.id.slice(0, 8)}...
                          </TableCell>
                          <TableCell className="text-xs text-slate-700">
                            {run.workflow_name || run.workflow_id.slice(0, 8)}
                          </TableCell>
                          <TableCell>
                            {st === "RECOVERING" ? (
                              <Badge className="bg-amber-500 hover:bg-amber-500 text-white text-xs flex items-center gap-1 w-fit">
                                <RotateCcw className="w-3 h-3 animate-spin" />
                                RECOVERING
                              </Badge>
                            ) : st === "RUNNING" ? (
                              <Badge className="bg-blue-600 hover:bg-blue-600 text-white text-xs">
                                RUNNING
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="text-xs">
                                {run.status}
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-xs font-mono font-bold text-slate-800">
                            v{run.fencing_token ?? 0}
                          </TableCell>
                          <TableCell className="font-mono text-xs text-slate-600">
                            {run.worker_id || "unassigned"}
                          </TableCell>
                          <TableCell className="text-xs text-slate-700 font-semibold">
                            {run.recovery_attempt_count ?? 0} / 3
                          </TableCell>
                          <TableCell className="text-xs text-slate-500 max-w-xs truncate" title={run.recovery_reason ?? ""}>
                            {run.recovery_reason || "None"}
                          </TableCell>
                          <TableCell className="text-right space-x-2">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs text-indigo-600 hover:text-indigo-800"
                              onClick={() => handleViewAuditLogs(run.id)}
                            >
                              Audit History
                            </Button>
                            <Button
                              variant="destructive"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => setRecoveringRun(run)}
                              disabled={st === "RECOVERING"}
                            >
                              Recover
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* CONFIRMATION MODAL: RESET CIRCUIT */}
      {resettingCircuit && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4">
            <h3 className="text-lg font-bold text-slate-900 flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-amber-500" />
              Reset Circuit Breaker?
            </h3>
            <p className="text-sm text-slate-600">
              Are you sure you want to manually reset the circuit breaker for{" "}
              <strong className="font-mono text-slate-900">{resettingCircuit.dependency_key}</strong> back to{" "}
              <strong className="text-emerald-700">CLOSED</strong>?
            </p>
            <p className="text-xs text-slate-500 bg-slate-50 p-3 rounded border border-slate-200">
              This will clear the failure count ({resettingCircuit.failure_count}) and immediately resume outbound requests to this external service.
            </p>
            <div className="flex justify-end gap-3 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setResettingCircuit(null)}
                disabled={isResetting}
              >
                Cancel
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={handleConfirmResetCircuit}
                disabled={isResetting}
                className="bg-emerald-600 hover:bg-emerald-700 text-white"
              >
                {isResetting ? "Resetting..." : "Confirm Reset"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* CONFIRMATION MODAL: MANUAL WORKFLOW RECOVERY */}
      {recoveringRun && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6 space-y-4">
            <h3 className="text-lg font-bold text-slate-900 flex items-center gap-2">
              <RotateCcw className="w-5 h-5 text-indigo-600" />
              Trigger Workflow Recovery?
            </h3>
            <p className="text-sm text-slate-600">
              Initiate immediate background recovery for workflow run{" "}
              <strong className="font-mono text-slate-900">{recoveringRun.id.slice(0, 8)}</strong>?
            </p>
            <div className="text-xs text-slate-600 space-y-1 bg-slate-50 p-3 rounded border border-slate-200">
              <div><strong>Current Worker:</strong> {recoveringRun.worker_id || "None"}</div>
              <div><strong>Fencing Token:</strong> v{recoveringRun.fencing_token ?? 0} &rarr; v{(recoveringRun.fencing_token ?? 0) + 1}</div>
              <div><strong>Recovery Attempt:</strong> {(recoveringRun.recovery_attempt_count ?? 0) + 1} of 3</div>
            </div>
            <p className="text-xs text-amber-700">
              This will increment the fencing token, fencing off any unresponsive zombie worker from committing stale partial results.
            </p>
            <div className="flex justify-end gap-3 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setRecoveringRun(null)}
                disabled={isTriggeringRecovery}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleConfirmRecoverRun}
                disabled={isTriggeringRecovery}
              >
                {isTriggeringRecovery ? "Initiating..." : "Execute Recovery"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* AUDIT LOG VIEWER MODAL */}
      {auditRunId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4 max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                <History className="w-5 h-5 text-indigo-600" />
                Recovery Audit History
              </h3>
              <button
                onClick={() => setAuditRunId(null)}
                className="text-slate-400 hover:text-slate-600 text-lg font-bold"
              >
                ×
              </button>
            </div>
            <p className="text-xs font-mono text-slate-500">
              Execution Run: {auditRunId}
            </p>

            <div className="flex-1 overflow-y-auto space-y-3">
              {loadingAudit ? (
                <div className="py-8 text-center text-slate-400 text-sm flex items-center justify-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading audit logs...
                </div>
              ) : auditLogs.length === 0 ? (
                <div className="py-8 text-center text-slate-400 text-sm">
                  No recovery events recorded for this workflow run yet.
                </div>
              ) : (
                auditLogs.map((log) => (
                  <div
                    key={log.id}
                    className="p-3 rounded-lg border border-slate-200 bg-slate-50/50 space-y-1.5 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-800">
                        Attempt #{log.recovery_attempt} &bull; Fencing Token v{log.fencing_token}
                      </span>
                      <Badge
                        variant={log.status === "completed" ? "default" : "outline"}
                        className="text-[10px]"
                      >
                        {log.status.toUpperCase()}
                      </Badge>
                    </div>
                    <div className="text-slate-600 grid grid-cols-2 gap-2">
                      <div><strong>Previous Worker:</strong> {log.previous_worker_id || "None"}</div>
                      <div><strong>Recovery Worker:</strong> {log.recovery_worker_id || "Unassigned"}</div>
                      <div><strong>Started:</strong> {new Date(log.started_at).toLocaleString()}</div>
                      <div><strong>Completed:</strong> {log.completed_at ? new Date(log.completed_at).toLocaleString() : "—"}</div>
                    </div>
                    <div className="text-slate-700 bg-white p-2 rounded border border-slate-100 font-mono text-[11px]">
                      Reason: {log.recovery_reason}
                    </div>
                  </div>
                ))
              )}
            </div>

            <div className="flex justify-end pt-3 border-t">
              <Button variant="outline" size="sm" onClick={() => setAuditRunId(null)}>
                Close
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
