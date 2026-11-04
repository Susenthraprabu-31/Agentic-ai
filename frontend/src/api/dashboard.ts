import { apiClient } from "./client";

export interface RecentActivityItem {
  id: string;
  workflow_id: string;
  workflow_name: string;
  status: string;
  duration_ms: number | null;
  created_at: string;
}

export interface DashboardSummaryResponse {
  total_workflows: number;
  county_sources: number;
  executions_today: number;
  total_executions: number;
  active_recipes: number;
  recent_activity: RecentActivityItem[];
}

export const dashboardApi = {
  /** Fetch dashboard KPI summary metrics and recent activity */
  async getSummary(): Promise<DashboardSummaryResponse> {
    const res = await apiClient.get<DashboardSummaryResponse>("/dashboard/summary");
    return res.data;
  },
};

