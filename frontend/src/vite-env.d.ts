/// <reference types="vite/client" />

interface ElectronAPI {
  getApiBaseUrl: () => Promise<string>;
  getDataDir: () => Promise<string>;
  openExternal: (url: string) => Promise<boolean>;
  isDesktop: boolean;
}

interface Window {
  electronAPI?: ElectronAPI;
}

declare module "*.css" {
  const content: Record<string, string>;
  export default content;
}

