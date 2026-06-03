import type { RouteLocationNormalizedLoaded, RouteLocationNormalized } from 'vue-router'
import { i18n } from '@/i18n'
import { APP_NAME } from '@/constants/branding'

export function routeTitle(route: RouteLocationNormalized | RouteLocationNormalizedLoaded): string {
  const { t } = i18n.global
  const title = typeof route.meta.titleKey === 'string' ? t(route.meta.titleKey) : route.meta.title
  return title ? `${title} · ${APP_NAME}` : APP_NAME
}

export function updateDocumentTitle(route: RouteLocationNormalized | RouteLocationNormalizedLoaded) {
  document.title = routeTitle(route)
}
