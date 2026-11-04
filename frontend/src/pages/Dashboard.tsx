import { useEffect, useState } from "react";
import {
  Layers,
  MapPin,
  PlayCircle,
  BookOpen,
  CheckCircle2,
  XCircle,
  AlertCircle,
  ArrowRight,
  Clock,
  Loader2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { healthApi } from "@/api/health";
import { dashboardApi, type RecentActivityItem } from "@/api/dashboard";
import { workflowsApi } from "@/api/workflows";
import { countySourcesApi } from "@/api/countySources";
import { recipeTemplatesApi } from "@/api/recipeTemplates";
import { executionsApi } from "@/api/executions";
import type { HealthStatus } from "@/types";

interface StatCard {
  label: string;
  value: string;
  icon: React.ElementType;
  iconColor: string;
  bgColor: string;
}

const quickActions = [
  { label: "Build Workflow", to: "/workflow-builder", description: "Design a new automation flow" },
  { label: "Add County Source", to: "/county-sources", description: "Register a new county data source" },
  { label: "Create Recipe", to: "/recipe-templates", description: "Define a new scraping template" },
  { label: "View Executions", to: "/execution-history", description: "Monitor recent run history" },
];

type ServiceStatus = "checking" | "healthy" | "degraded" | "offline";

interface ServiceHealth {
  name: string;
  status: ServiceStatus;
}

function StatusIcon({ status }: { status: ServiceStatus }) {
  if (status === "checking") return <AlertCircle className="w-4 h-4 text-amber-500 animate-pulse" />;
  if (status === "healthy") return <CheckCircle2 className="w-4 h-4 text-emerald-500" />;
  if (status === "degraded") return <AlertCircle className="w-4 h-4 text-amber-500" />;
  return <XCircle className="w-4 h-4 text-red-500" />;
}

function statusVariant(status: ServiceStatus): "success" | "warning" | "destructive" | "outline" {
  if (status === "healthy") return "success";
  if (status === "degraded") return "warning";
  if (status === "offline") return "destructive";
  return "outline";
}

function isHealthyStatus(val?: string): boolean {
  if (!val) return false;
  const s = val.toLowerCase().trim();
  return s === "healthy" || s === "ok";
}

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

function formatRelativeTime(dateString: string): string {
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    return date.toLocaleDateString();
  } catch {
    return dateString;
  }
}

export default function Dashboard() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [services, setServices] = useState<ServiceHealth[]>([
    { name: "Backend API", status: "checking" },
    { name: "Database", status: "checking" },
    { name: "Redis", status: "checking" },
  ]);

  const [counts, setCounts] = useState({
    totalWorkflows: 0,
    countySources: 0,
    executionsToday: 0,
    activeRecipes: 0,
  });

  const [recentActivity, setRecentActivity] = useState<RecentActivityItem[]>([]);
  const [, setIsLoadingActivity] = useState(true);

  const stats: StatCard[] = [
    {
      label: "Total Workflows",
      value: counts.totalWorkflows.toString(),
      icon: Layers,
      iconColor: "text-blue-600",
      bgColor: "bg-blue-50",
    },
    {
      label: "County Sources",
      value: counts.countySources.toString(),
      icon: MapPin,
      iconColor: "text-emerald-600",
      bgColor: "bg-emerald-50",
    },
    {
      label: "Executions Today",
      value: counts.executionsToday.toString(),
      icon: PlayCircle,
      iconColor: "text-violet-600",
      bgColor: "bg-violet-50",
    },
    {
      label: "Active Recipes",
      value: counts.activeRecipes.toString(),
      icon: BookOpen,
      iconColor: "text-amber-600",
      bgColor: "bg-amber-50",
    },
  ];

  useEffect(() => {
    // 1. Fetch Health Status
    healthApi
      .getHealth()
      .then((res) => {
        setHealth(res.data);
        const backendHealthy = isHealthyStatus(res.data.status);
        const dbHealthy = isHealthyStatus(res.data.database);
        const redisHealthy = isHealthyStatus(res.data.redis);

        setServices([
          {
            name: "Backend API",
            status: backendHealthy ? "healthy" : "degraded",
          },
          {
            name: "Database",
            status: dbHealthy ? "healthy" : "offline",
          },
          {
            name: "Redis",
            status: redisHealthy ? "healthy" : "offline",
          },
        ]);
      })
      .catch(() => {
        setServices([
          { name: "Backend API", status: "offline" },
          { name: "Database", status: "offline" },
          { name: "Redis", status: "offline" },
        ]);
      });

    // 2. Fetch Dashboard Summary Metrics and Activity
    dashboardApi
      .getSummary()
      .then((data) => {
        setCounts({
          totalWorkflows: data.total_workflows,
          countySources: data.county_sources,
          executionsToday: data.executions_today,
          activeRecipes: data.active_recipes,
        });
        setRecentActivity(data.recent_activity || []);
      })
      .catch(() => {
        // Fallback to individual API endpoints if needed
        Promise.allSettled([
          workflowsApi.list({ limit: 1 }),
          countySourcesApi.list({ size: 1 }),
          recipeTemplatesApi.getRecipes({ limit: 1 }),
          executionsApi.list({ limit: 10 }),
        ]).then(([wf, cs, rec, ex]) => {
          const wfCount = wf.status === "fulfilled" ? wf.value.total : 0;
          const csCount = cs.status === "fulfilled" ? cs.value.total : 0;
          const recCount = rec.status === "fulfilled" ? rec.value.total : 0;
          let exToday = 0;
          let acts: RecentActivityItem[] = [];
          if (ex.status === "fulfilled") {
            const todayStr = new Date().toDateString();
            exToday = ex.value.items.filter(
              (r) => new Date(r.created_at).toDateString() === todayStr
            ).length;
            acts = ex.value.items.slice(0, 5).map((r) => ({
              id: r.id,
              workflow_id: r.workflow_id,
              workflow_name: r.workflow_name || "Workflow Execution",
              status: r.status,
              duration_ms: r.duration_ms,
              created_at: r.created_at,
            }));
          }
          setCounts({
            totalWorkflows: wfCount,
            countySources: csCount,
            executionsToday: exToday,
            activeRecipes: recCount,
          });
          setRecentActivity(acts);
        });
      })
      .finally(() => {
        setIsLoadingActivity(false);
      });
  }, []);

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {stats.map((stat) => (
          <Card key={stat.label}>
            <CardContent className="p-5">
              <div className="flex items-center gap-4">
                <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${stat.bgColor}`}>
                  <stat.icon className={`w-5 h-5 ${stat.iconColor}`} />
                </div>
                <div>
                  <p className="text-2xl font-bold text-slate-900">{stat.value}</p>
                  <p className="text-sm text-muted-foreground">{stat.label}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Quick Actions & Recent Activity */}
        <div className="lg:col-span-2 space-y-4">
          <h2 className="text-base font-semibold text-slate-900">Quick Actions</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {quickActions.map((action) => (
              <Card key={action.to} className="hover:shadow-md transition-shadow cursor-pointer">
                <CardContent className="p-4">
                  <Link to={action.to} className="flex items-center justify-between group">
                    <div>
                      <p className="font-medium text-slate-900 group-hover:text-blue-600 transition-colors">
                        {action.label}
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5">{action.description}</p>
                    </div>
                    <ArrowRight className="w-4 h-4 text-slate-400 group-hover:text-blue-500 transition-colors" />
                  </Link>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Recent Activity */}
          <Card className="mt-2">
            <CardHeader className="pb-3 flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">Recent Activity</CardTitle>
                <CardDescription>Latest workflow executions and events</CardDescription>
              </div>
              {recentActivity.length > 0 && (
                <Button variant="ghost" size="sm" asChild className="text-xs text-blue-600 hover:text-blue-700">
                  <Link to="/execution-history" className="flex items-center gap-1">
                    View All <ArrowRight className="w-3.5 h-3.5" />
                  </Link>
                </Button>
              )}
            </CardHeader>
            <CardContent>
              {recentActivity.length > 0 ? (
                <div className="divide-y divide-slate-100">
                  {recentActivity.map((run) => (
                    <div
                      key={run.id}
                      className="py-3 first:pt-0 last:pb-0 flex items-center justify-between hover:bg-slate-50/50 -mx-2 px-2 rounded-lg transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="flex items-center justify-center w-8 h-8 rounded-full bg-slate-100 shrink-0">
                          <PlayCircle className="w-4 h-4 text-slate-600" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-slate-900 truncate">
                            {run.workflow_name}
                          </p>
                          <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
                            <span>Run: {run.id.slice(0, 8)}...</span>
                            <span>•</span>
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {formatRelativeTime(run.created_at)}
                            </span>
                            {run.duration_ms !== null && (
                              <>
                                <span>•</span>
                                <span>{formatDuration(run.duration_ms)}</span>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {runStatusBadge(run.status)}
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-400 hover:text-blue-600" asChild>
                          <Link to={`/execution-results?id=${run.id}`} title="View Results">
                            <ArrowRight className="w-4 h-4" />
                          </Link>
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-10 text-center text-muted-foreground">
                  <PlayCircle className="w-10 h-10 mb-3 opacity-30" />
                  <p className="text-sm font-medium">No recent activity</p>
                  <p className="text-xs mt-1">Run a workflow to see activity here</p>
                  <Button variant="outline" size="sm" className="mt-4" asChild>
                    <Link to="/workflow-builder">Build First Workflow</Link>
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* System Health */}
        <div>
          <h2 className="text-base font-semibold text-slate-900 mb-4">System Health</h2>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
                Service Status
              </CardTitle>
              {health && (
                <p className="text-xs text-muted-foreground">
                  v{health.version} · {health.environment}
                </p>
              )}
            </CardHeader>
            <CardContent className="space-y-3">
              {services.map((svc) => (
                <div key={svc.name} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <StatusIcon status={svc.status} />
                    <span className="text-sm text-slate-700">{svc.name}</span>
                  </div>
                  <Badge variant={statusVariant(svc.status)} className="capitalize text-xs">
                    {svc.status}
                  </Badge>
                </div>
              ))}
              {health?.timestamp && (
                <p className="text-xs text-muted-foreground pt-2 border-t">
                  Last checked:{" "}
                  {new Date(health.timestamp).toLocaleTimeString()}
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
