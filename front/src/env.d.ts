declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}
declare module '*.svg' {
  const src: string
  export default src
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

interface Window {
  championDesktop?: { localOperations?: boolean;
    pendingHandoff?: () => Promise<string>;
    finishHandoff?: (id: string) => Promise<void>;
    onHandoff?: (callback: (id: string) => void) => () => void;
    openAlibabaAuthorization?: (url: string) => Promise<void>;
    savedLogin?: () => Promise<{ available: boolean; email: string; password: string; auto?: boolean }>;
    rememberLogin?: (options: { email: string; password: string; remember: boolean; auto: boolean }) => Promise<void>;
     onConnectionChanged?: (callback: () => void) => () => void; configureModels?: (models: Record<string, Record<string, string>>) => Promise<{ models: Record<string, Record<string, string>>; fingerprints: Record<string, string> }>; openNewAPI?: (target: 'keys' | 'wallet' | 'usage') => Promise<void>; openLicenses: () => Promise<void> }
}
