import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    requiresAdmin?: boolean
    title?: string
    titleKey?: string
    descriptionKey?: string
    breadcrumbs?: Array<{
      label: string
      to?: string
    }>
    icon?: string
    hideInMenu?: boolean
    requiresPayment?: boolean
  }
}
