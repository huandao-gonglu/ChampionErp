import type { RouteLocationRaw } from 'vue-router'

export function loginRedirectTarget(path: string): RouteLocationRaw {
  return { path: '/', query: path && path !== '/' ? { redirect: path } : undefined }
}
