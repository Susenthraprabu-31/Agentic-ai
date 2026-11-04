import { apiClient } from "./client";
import type {
  Workflow,
  WorkflowDefinition,
  WorkflowValidationResponse,
  WorkflowVersion,
} from "../types";

export interface WorkflowListParams {
  search?: string;
  is_active?: boolean;
  county_source_id?: string;
  skip?: number;
  limit?: number;
}

export const workflowsApi = {
  /** Fetch list of workflows */
  async list(params?: WorkflowListParams): Promise<{ items: Workflow[]; total: number }> {
    const res = await apiClient.get<{ items: Workflow[]; total: number }>("/workflows", {
      params,
    });
    return res.data;
  },

  /** Get a single workflow by ID with its versions */
  async getById(id: string): Promise<Workflow> {
    const res = await apiClient.get<Workflow>(`/workflows/${id}`);
    return res.data;
  },

  /** Create a new workflow */
  async create(data: {
    name: string;
    description?: string;
    county_source_id?: string;
    initial_definition?: WorkflowDefinition;
  }): Promise<Workflow> {
    const res = await apiClient.post<Workflow>("/workflows", data);
    return res.data;
  },

  /** Update workflow metadata */
  async update(
    id: string,
    data: {
      name?: string;
      description?: string;
      is_active?: boolean;
      county_source_id?: string;
    }
  ): Promise<Workflow> {
    const res = await apiClient.put<Workflow>(`/workflows/${id}`, data);
    return res.data;
  },

  /** Delete a workflow */
  async delete(id: string): Promise<void> {
    await apiClient.delete(`/workflows/${id}`);
  },

  /** List versions for a workflow */
  async listVersions(workflowId: string): Promise<WorkflowVersion[]> {
    const res = await apiClient.get<WorkflowVersion[]>(`/workflows/${workflowId}/versions`);
    return res.data;
  },

  /** Create a new version for a workflow */
  async createVersion(
    workflowId: string,
    data: {
      definition: WorkflowDefinition;
      changelog?: string;
      is_current?: boolean;
    }
  ): Promise<WorkflowVersion> {
    const res = await apiClient.post<WorkflowVersion>(
      `/workflows/${workflowId}/versions`,
      data
    );
    return res.data;
  },

  /** Get specific version definition */
  async getVersion(workflowId: string, versionId: string): Promise<WorkflowVersion> {
    const res = await apiClient.get<WorkflowVersion>(
      `/workflows/${workflowId}/versions/${versionId}`
    );
    return res.data;
  },

  /** Set a version as the active target */
  async setCurrentVersion(
    workflowId: string,
    versionId: string
  ): Promise<WorkflowVersion> {
    const res = await apiClient.post<WorkflowVersion>(
      `/workflows/${workflowId}/versions/${versionId}/set-current`
    );
    return res.data;
  },

  /** Validate workflow definition (DAG, cycles, reachability, partial success) */
  async validate(definition: WorkflowDefinition): Promise<WorkflowValidationResponse> {
    const res = await apiClient.post<WorkflowValidationResponse>(
      "/workflows/validate",
      { definition }
    );
    return res.data;
  },
};

