import { apiClient } from "./client";
import type {
  CountySource,
  CountySourceListResponse,
  SourceConfiguration,
} from "@/types";

export interface ListCountySourcesParams {
  page?: number;
  size?: number;
  search?: string;
  state?: string;
  status?: string;
  is_active?: boolean;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export const countySourcesApi = {
  /** Fetch paginated list of county sources */
  list: async (params?: ListCountySourcesParams): Promise<CountySourceListResponse> => {
    const res = await apiClient.get<CountySourceListResponse>("/county-sources", { params });
    return res.data;
  },

  /** Get a county source by ID */
  getById: async (id: string): Promise<CountySource> => {
    const res = await apiClient.get<CountySource>(`/county-sources/${id}`);
    return res.data;
  },

  /** Create a new county source */
  create: async (payload: Partial<CountySource>): Promise<CountySource> => {
    const res = await apiClient.post<CountySource>("/county-sources", payload);
    return res.data;
  },

  /** Update an existing county source */
  update: async (id: string, payload: Partial<CountySource>): Promise<CountySource> => {
    const res = await apiClient.put<CountySource>(`/county-sources/${id}`, payload);
    return res.data;
  },

  /** Delete a county source */
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/county-sources/${id}`);
  },

  // -------------------------------------------------------------------------
  // Source Configurations (Assessor / Tax Automation Selectors)
  // -------------------------------------------------------------------------

  /** List configurations for a county source */
  listConfigurations: async (sourceId: string): Promise<SourceConfiguration[]> => {
    const res = await apiClient.get<SourceConfiguration[]>(
      `/county-sources/${sourceId}/configurations`
    );
    return res.data;
  },

  /** Create an Assessor or Tax configuration */
  createConfiguration: async (
    sourceId: string,
    payload: Partial<SourceConfiguration>
  ): Promise<SourceConfiguration> => {
    const res = await apiClient.post<SourceConfiguration>(
      `/county-sources/${sourceId}/configurations`,
      payload
    );
    return res.data;
  },

  /** Update a configuration */
  updateConfiguration: async (
    sourceId: string,
    configId: string,
    payload: Partial<SourceConfiguration>
  ): Promise<SourceConfiguration> => {
    const res = await apiClient.put<SourceConfiguration>(
      `/county-sources/${sourceId}/configurations/${configId}`,
      payload
    );
    return res.data;
  },

  /** Delete a configuration */
  deleteConfiguration: async (sourceId: string, configId: string): Promise<void> => {
    await apiClient.delete(`/county-sources/${sourceId}/configurations/${configId}`);
  },

  /** Analyze county search portal with AI and save recipe actions to DB */
  analyzeRecipe: async (
    sourceId: string,
    params?: { record_type?: string; portal_url?: string }
  ): Promise<any> => {
    const res = await apiClient.post(
      `/county-sources/${sourceId}/analyze-recipe`,
      params || {},
      { params }
    );
    return res.data;
  },
};

