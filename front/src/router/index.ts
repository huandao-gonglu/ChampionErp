import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { updateDocumentTitle } from './title'

const workflowComponent = () => import('@/views/workflow/WorkflowView.vue')

const workflowRoutes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'WorkflowHome',
    component: workflowComponent,
    meta: {
      titleKey: 'routes.workflow.title',
      descriptionKey: 'routes.workflow.description',
      icon: '▦',
    },
  },
]

const legacyWorkflowEntries = [
  { path: '/collect', tab: 'collect', title: '采集' },
  { path: '/research', tab: 'research', title: '选品调研' },
  { path: '/library', tab: 'library', title: '商品库' },
  { path: '/drafts', tab: 'drafts', title: '草稿箱' },
  { path: '/ml-user-products', tab: 'mlUserProducts', title: 'ML User Products' },
  { path: '/edit', tab: 'library', title: '商品库' },
  { path: '/media', tab: 'library', title: '商品库' },
  { path: '/pricing', tab: 'drafts', title: '草稿箱' },
  { path: '/publish', tab: 'publish', title: '发布队列' },
  { path: '/settings', tab: 'auth', title: '设置' },
  { path: '/auth', tab: 'auth', title: '授权' },
  { path: '/logs', tab: 'logs', title: '日志' },
  { path: '/pending', tab: 'pending', title: '待处理' },
] as const

const legacyEntryRoutes: RouteRecordRaw[] = legacyWorkflowEntries.map((entry) => ({
  path: entry.path,
  redirect: (to) => ({
    name: 'WorkflowHome',
    query: {
      ...to.query,
      tab: String(to.query.tab || entry.tab),
    },
  }),
  meta: { title: entry.title, hideInMenu: true },
}))

const routes: RouteRecordRaw[] = [
  // Setup Routes
  // Public Routes
  ...workflowRoutes,
  // Auth Routes
  // User Routes
  ...legacyEntryRoutes,
  {
    path: '/aiWork',
    name: 'AiWork',
    component: () => import('@/views/AiWorkView.vue'),
    meta: {
      title: 'AI Work',
      hideInMenu: true,
    },
  },
  // Admin Routes
  // 404 Routes
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/NotFoundView.vue'),
    meta: {
      titleKey: 'routes.notFound.title',
      descriptionKey: 'routes.notFound.description',
      hideInMenu: true,
    },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach((to) => {
  updateDocumentTitle(to)
})

router.afterEach((to) => {
  updateDocumentTitle(to)
})

router.onError((error) => {
  const message = String(error?.message || '')
  if (/Failed to fetch dynamically imported module|Importing a module script failed/i.test(message)) {
    const key = 'champion-erp-vite-reloaded'
    if (!sessionStorage.getItem(key)) {
      sessionStorage.setItem(key, '1')
      window.location.reload()
    }
  }
})

export default router
