import { apiClient } from "@/api/client";

export type MiamiDadeAuthStatus = {
  enabled: boolean;
  configured: boolean;
  file_name: string;
  cookie_count: number;
  origin_count: number;
  message: string;
  profile_configured?: boolean;
  profile_path_hint?: string;
  profile_updated_at?: string | null;
  auth_updated_at?: string | null;
  policy_file?: string | null;
};

export const browserToolsApi = {
  getMiamiDadeAuthStatus: async (): Promise<MiamiDadeAuthStatus> => {
    const { data } = await apiClient.get<MiamiDadeAuthStatus>(
      "/browser-tools/miami-dade-tax-auth/status"
    );
    return data;
  },

  uploadMiamiDadeAuth: async (file: File): Promise<{ ok: boolean; message: string; cookie_count: number }> => {
    const form = new FormData();
    form.append("file", file);
    const { data } = await apiClient.post("/browser-tools/miami-dade-tax-auth/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120000,
    });
    return data;
  },
};