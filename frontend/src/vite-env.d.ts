/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SETTINGS_ALLOWED_CLIENT_IDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
