import type { Plugin } from 'vite'

export function desktopDevelopment(options: { enabled: boolean; backendUrl: string }): Plugin
