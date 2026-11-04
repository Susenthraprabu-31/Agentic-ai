import { apiClient } from './client';
import type {
  UITemplate,
  TemplateVersion,
  Recipe,
  RecipeVersion,
  ValidationCase,
  CompatibilityCheckRequest,
  CompatibilityCheckResponse,
  PaginatedResponse,
} from '../types';

export const recipeTemplatesApi = {
  // ─── UI Templates ─────────────────────────────────────────────────────────
  async getTemplates(params?: {
    search?: string;
    pattern_type?: string;
    status?: string;
    skip?: number;
    limit?: number;
  }): Promise<PaginatedResponse<UITemplate>> {
    const response = await apiClient.get<PaginatedResponse<UITemplate>>('/recipe-templates', {
      params,
    });
    return response.data;
  },

  async getTemplate(id: string): Promise<UITemplate> {
    const response = await apiClient.get<UITemplate>(`/recipe-templates/${id}`);
    return response.data;
  },

  async createTemplate(data: {
    name: string;
    description?: string;
    pattern_type?: string;
    supported_source_types?: string[];
    supported_search_methods?: string[];
    required_configuration_fields?: string[];
    default_config_schema?: Record<string, unknown>;
    initial_version_definition?: Record<string, unknown>;
  }): Promise<UITemplate> {
    const response = await apiClient.post<UITemplate>('/recipe-templates', data);
    return response.data;
  },

  async updateTemplate(id: string, data: Partial<UITemplate>): Promise<UITemplate> {
    const response = await apiClient.put<UITemplate>(`/recipe-templates/${id}`, data);
    return response.data;
  },

  async deleteTemplate(id: string): Promise<void> {
    await apiClient.delete(`/recipe-templates/${id}`);
  },

  // ─── Template Versions ────────────────────────────────────────────────────
  async getTemplateVersions(templateId: string): Promise<TemplateVersion[]> {
    const response = await apiClient.get<TemplateVersion[]>(
      `/recipe-templates/${templateId}/versions`
    );
    return response.data;
  },

  async createTemplateVersion(
    templateId: string,
    data: {
      definition: Record<string, unknown>;
      changelog?: string;
      is_current?: boolean;
    }
  ): Promise<TemplateVersion> {
    const response = await apiClient.post<TemplateVersion>(
      `/recipe-templates/${templateId}/versions`,
      data
    );
    return response.data;
  },

  // ─── Recipes ──────────────────────────────────────────────────────────────
  async getRecipes(params?: {
    county_source_id?: string;
    record_type?: string;
    status?: string;
    search?: string;
    skip?: number;
    limit?: number;
  }): Promise<PaginatedResponse<Recipe>> {
    const response = await apiClient.get<PaginatedResponse<Recipe>>('/recipes', {
      params,
    });
    return response.data;
  },

  async getRecipe(id: string): Promise<Recipe> {
    const response = await apiClient.get<Recipe>(`/recipes/${id}`);
    return response.data;
  },

  async createRecipe(data: {
    name: string;
    county_source_id: string;
    source_configuration_id?: string;
    ui_template_id?: string;
    record_type: 'assessor' | 'tax';
    description?: string;
    initial_definition?: Record<string, unknown>;
    template_version_id?: string;
  }): Promise<Recipe> {
    const response = await apiClient.post<Recipe>('/recipes', data);
    return response.data;
  },

  async updateRecipe(
    id: string,
    data: {
      name?: string;
      description?: string;
      source_configuration_id?: string;
      ui_template_id?: string;
      status?: string;
    }
  ): Promise<Recipe> {
    const response = await apiClient.put<Recipe>(`/recipes/${id}`, data);
    return response.data;
  },

  async deleteRecipe(id: string): Promise<void> {
    await apiClient.delete(`/recipes/${id}`);
  },

  // ─── Recipe Versions ──────────────────────────────────────────────────────
  async getRecipeVersions(recipeId: string): Promise<RecipeVersion[]> {
    const response = await apiClient.get<RecipeVersion[]>(`/recipes/${recipeId}/versions`);
    return response.data;
  },

  async getRecipeVersion(recipeId: string, versionId: string): Promise<RecipeVersion> {
    const response = await apiClient.get<RecipeVersion>(
      `/recipes/${recipeId}/versions/${versionId}`
    );
    return response.data;
  },

  async createRecipeVersion(
    recipeId: string,
    data: {
      definition: Record<string, unknown>;
      changelog?: string;
      template_version_id?: string;
    }
  ): Promise<RecipeVersion> {
    const response = await apiClient.post<RecipeVersion>(
      `/recipes/${recipeId}/versions`,
      data
    );
    return response.data;
  },

  async updateRecipeVersion(
    recipeId: string,
    versionId: string,
    data: {
      definition?: Record<string, unknown>;
      changelog?: string;
    }
  ): Promise<RecipeVersion> {
    const response = await apiClient.put<RecipeVersion>(
      `/recipes/${recipeId}/versions/${versionId}`,
      data
    );
    return response.data;
  },

  async updateRecipeLifecycle(
    recipeId: string,
    versionId: string,
    status: string
  ): Promise<RecipeVersion> {
    const response = await apiClient.patch<RecipeVersion>(
      `/recipes/${recipeId}/versions/${versionId}/lifecycle`,
      { status }
    );
    return response.data;
  },

  async publishRecipeVersion(
    recipeId: string,
    versionId: string
  ): Promise<{ message: string; recipe_id: string; version_id: string; version_number: number }> {
    const response = await apiClient.post(
      `/recipes/${recipeId}/versions/${versionId}/publish`
    );
    return response.data;
  },

  // ─── Validation Cases ─────────────────────────────────────────────────────
  async getValidationCases(recipeId: string): Promise<ValidationCase[]> {
    const response = await apiClient.get<ValidationCase[]>(
      `/recipes/${recipeId}/validation-cases`
    );
    return response.data;
  },

  async createValidationCase(
    recipeId: string,
    data: {
      name: string;
      recipe_version_id?: string;
      description?: string;
      input_data: Record<string, unknown>;
      expected_output: Record<string, unknown>;
    }
  ): Promise<ValidationCase> {
    const response = await apiClient.post<ValidationCase>(
      `/recipes/${recipeId}/validation-cases`,
      data
    );
    return response.data;
  },

  async updateValidationCase(
    recipeId: string,
    caseId: string,
    data: Partial<ValidationCase>
  ): Promise<ValidationCase> {
    const response = await apiClient.put<ValidationCase>(
      `/recipes/${recipeId}/validation-cases/${caseId}`,
      data
    );
    return response.data;
  },

  async deleteValidationCase(recipeId: string, caseId: string): Promise<void> {
    await apiClient.delete(`/recipes/${recipeId}/validation-cases/${caseId}`);
  },

  // ─── Compatibility ───────────────────────────────────────────────────────
  async checkCompatibility(
    data: CompatibilityCheckRequest
  ): Promise<CompatibilityCheckResponse> {
    const response = await apiClient.post<CompatibilityCheckResponse>(
      '/recipes/check-compatibility',
      data
    );
    return response.data;
  },
};
