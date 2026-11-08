import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    description?: string
    breadcrumbs?: Array<{
      label: string
      to?: string
    }>
    icon?: string
    hideInMenu?: boolean
  }
}
