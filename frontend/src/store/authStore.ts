import { create } from "zustand";
import { authApi } from "@/api/auth";
import type { LoginPayload, RegisterPayload, User } from "@/types";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;

  setAuth: (user: User, accessToken: string) => void;
  setAccessToken: (token: string) => void;
  setUser: (user: User) => void;
  clearAuth: () => void;
  setError: (error: string | null) => void;

  login: (payload: LoginPayload) => Promise<User>;
  register: (payload: RegisterPayload) => Promise<User>;
  logout: () => Promise<void>;
  initializeAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  isAuthenticated: false,
  isLoading: true, // starts true while checking initial session
  error: null,

  setAuth: (user, accessToken) =>
    set({
      user,
      accessToken,
      isAuthenticated: true,
      isLoading: false,
      error: null,
    }),

  setAccessToken: (accessToken) =>
    set({
      accessToken,
      isAuthenticated: !!accessToken,
      isLoading: false,
    }),

  setUser: (user) =>
    set({
      user,
    }),

  clearAuth: () =>
    set({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
    }),

  setError: (error) => set({ error }),

  login: async (payload: LoginPayload) => {
    set({ isLoading: true, error: null });
    try {
      const data = await authApi.login(payload);
      let currentUser = data.user;
      if (!currentUser) {
        // Fallback fetch me if not embedded in login response
        set({ accessToken: data.access_token });
        currentUser = await authApi.getMe();
      }
      set({
        user: currentUser,
        accessToken: data.access_token,
        isAuthenticated: true,
        isLoading: false,
        error: null,
      });
      return currentUser;
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to sign in. Please verify your credentials.";
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  register: async (payload: RegisterPayload) => {
    set({ isLoading: true, error: null });
    try {
      const user = await authApi.register(payload);
      set({ isLoading: false, error: null });
      return user;
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error).message ??
        "Failed to register account.";
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore network errors on logout
    } finally {
      get().clearAuth();
    }
  },

  initializeAuth: async () => {
    set({ isLoading: true });
    try {
      // Attempt silent refresh via HttpOnly cookie
      const data = await authApi.refresh();
      let user = data.user;
      if (!user) {
        set({ accessToken: data.access_token });
        user = await authApi.getMe();
      }
      set({
        user,
        accessToken: data.access_token,
        isAuthenticated: true,
        isLoading: false,
        error: null,
      });
    } catch {
      // No active session or refresh cookie expired
      set({
        user: null,
        accessToken: null,
        isAuthenticated: false,
        isLoading: false,
      });
    }
  },
}));
