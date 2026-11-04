import { apiClient } from "./client";
import type {
  BrowserActivityResponse,
  BrowserSession,
  DocumentArtifact,
  ExecutionDownloadStatus,
  ExecutionResultsResponse,
  NodeRun,
  WorkflowRun,
} from "../types";

export interface ListExecutionsParams {
  workflow_id?: string;
  status?: string;
  skip?: number;
  limit?: number;
}

export const executionsApi = {
  /** Fetch paginated list of workflow execution runs */
  async list(params?: ListExecutionsParams): Promise<{ items: WorkflowRun[]; total: number }> {
    const res = await apiClient.get<{ items: WorkflowRun[]; total: number }>("/executions", {
      params,
    });
    return res.data;
  },

  /** Get execution details by run ID */
  async getById(runId: string): Promise<WorkflowRun> {
    const res = await apiClient.get<WorkflowRun>(`/executions/${runId}`);
    return res.data;
  },

  /** Get aggregated execution results and normalized property data */
  async getResults(runId: string): Promise<ExecutionResultsResponse> {
    const res = await apiClient.get<ExecutionResultsResponse>(`/executions/${runId}/results`);
    return res.data;
  },

  /** List individual node run execution records */
  async listNodes(runId: string): Promise<NodeRun[]> {
    const res = await apiClient.get<NodeRun[]>(`/executions/${runId}/nodes`);
    return res.data;
  },

  /** Get retrieved document artifacts for execution */
  async getDocuments(runId: string): Promise<DocumentArtifact[]> {
    const res = await apiClient.get<DocumentArtifact[]>(`/executions/${runId}/documents`);
    return res.data;
  },

  /** Trigger background workflow execution (returns 202 Accepted) */
  async executeWorkflow(
    workflowId: string,
    data: {
      workflow_version_id?: string;
      input_data?: Record<string, unknown>;
    }
  ): Promise<WorkflowRun> {
    const res = await apiClient.post<WorkflowRun>(`/workflows/${workflowId}/execute`, data);
    return res.data;
  },

  /** Request cancellation of an ongoing execution */
  async cancel(runId: string): Promise<WorkflowRun> {
    const res = await apiClient.post<WorkflowRun>(`/executions/${runId}/cancel`);
    return res.data;
  },

  /** Get browser sessions for execution */
  async getBrowserSessions(runId: string): Promise<BrowserSession[]> {
    const res = await apiClient.get<BrowserSession[]>(`/executions/${runId}/browser-sessions`);
    return res.data;
  },

  /** Get aggregated document download status for execution */
  async getDownloadStatus(runId: string): Promise<ExecutionDownloadStatus> {
    const res = await apiClient.get<ExecutionDownloadStatus>(`/executions/${runId}/download-status`);
    return res.data;
  },

  /** Get live browser activity and step execution log */
  async getBrowserActivity(runId: string): Promise<BrowserActivityResponse> {
    const res = await apiClient.get<BrowserActivityResponse>(`/executions/${runId}/browser-activity`);
    return res.data;
  },
};

