import type { RouteLocationNormalizedLoaded, RouteLocationNormalized } from 'vue-router'
import { APP_NAME } from '@/constants/branding'

export function routeTitle(route: RouteLocationNormalized | RouteLocationNormalizedLoaded): string {
  const title = route.meta.title
  return title ? `${title} · ${APP_NAME}` : APP_NAME
}

export function updateDocumentTitle(route: RouteLocationNormalized | RouteLocationNormalizedLoaded) {
  document.title = routeTitle(route)
}
