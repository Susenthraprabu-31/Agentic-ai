import { apiClient } from "./client";
import type {
  CircuitBreakerState,
  RecoveryAuditRecord,
  WorkerHealthResponse,
  WorkerRecord,
  WorkflowRun,
} from "../types";

export const monitoringApi = {
  /** List all registered Celery workers */
  async getWorkers(): Promise<WorkerRecord[]> {
    const res = await apiClient.get<WorkerRecord[]>("/workers");
    return res.data;
  },

  /** Get aggregated worker cluster health */
  async getWorkerHealth(): Promise<WorkerHealthResponse> {
    const res = await apiClient.get<WorkerHealthResponse>("/workers/health");
    return res.data;
  },

  /** Get specific worker record */
  async getWorker(workerId: string): Promise<WorkerRecord> {
    const res = await apiClient.get<WorkerRecord>(`/workers/${workerId}`);
    return res.data;
  },

  /** List all external dependency circuit breakers */
  async getCircuitBreakers(): Promise<CircuitBreakerState[]> {
    const res = await apiClient.get<CircuitBreakerState[]>("/circuit-breakers");
    return res.data;
  },

  /** Get specific circuit breaker state */
  async getCircuitBreaker(dependencyKey: string): Promise<CircuitBreakerState> {
    const res = await apiClient.get<CircuitBreakerState>(`/circuit-breakers/${dependencyKey}`);
    return res.data;
  },

  /** Manually reset a tripped circuit breaker to CLOSED */
  async resetCircuitBreaker(dependencyKey: string): Promise<CircuitBreakerState> {
    const res = await apiClient.post<CircuitBreakerState>(`/circuit-breakers/${dependencyKey}/reset`);
    return res.data;
  },

  /** Get recovery audit log history for an execution run */
  async getExecutionRecovery(runId: string): Promise<RecoveryAuditRecord[]> {
    const res = await apiClient.get<RecoveryAuditRecord[]>(`/executions/${runId}/recovery`);
    return res.data;
  },

  /** Manually trigger recovery of an interrupted workflow run */
  async triggerManualRecovery(runId: string): Promise<{ success: boolean; message: string; run: WorkflowRun }> {
    const res = await apiClient.post<{ success: boolean; message: string; run: WorkflowRun }>(
      `/executions/${runId}/recover`
    );
    return res.data;
  },
};

