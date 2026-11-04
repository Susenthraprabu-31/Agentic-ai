import { apiClient } from "./client";
import type { LoginPayload, RegisterPayload, TokenResponse, User } from "@/types";

export const authApi = {
  /** Authenticate user credentials and receive access token + HttpOnly cookie */
  login: async (payload: LoginPayload): Promise<TokenResponse> => {
    const res = await apiClient.post<TokenResponse>("/auth/login", payload);
    return res.data;
  },

  /** Register a new user */
  register: async (payload: RegisterPayload): Promise<User> => {
    const res = await apiClient.post<User>("/auth/register", payload);
    return res.data;
  },

  /** Rotate refresh token and obtain new access token */
  refresh: async (refreshToken?: string): Promise<TokenResponse> => {
    const body = refreshToken ? { refresh_token: refreshToken } : {};
    const res = await apiClient.post<TokenResponse>("/auth/refresh", body);
    return res.data;
  },

  /** Invalidate session and clear refresh token cookie */
  logout: async (refreshToken?: string): Promise<void> => {
    const body = refreshToken ? { refresh_token: refreshToken } : {};
    await apiClient.post("/auth/logout", body);
  },

  /** Fetch current authenticated user profile */
  getMe: async (): Promise<User> => {
    const res = await apiClient.get<User>("/auth/me");
    return res.data;
  },
};

