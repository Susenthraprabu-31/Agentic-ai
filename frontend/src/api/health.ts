import { apiClient } from "./client";
import type { HealthStatus } from "@/types";

export const healthApi = {
  getHealth: () => apiClient.get<HealthStatus>("/health"),
  getLiveness: () => apiClient.get<{ status: string }>("/health/live"),
  getReadiness: () => apiClient.get<HealthStatus>("/health/ready"),
};
