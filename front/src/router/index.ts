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

const legacyEntryRoutes: RouteRecordRaw[] = [
  {
    path: '/collect',
    component: workflowComponent,
    meta: { title: '采集', hideInMenu: true },
  },
  {
    path: '/library',
    component: workflowComponent,
    meta: { title: '商品库', hideInMenu: true },
  },
  {
    path: '/edit',
    redirect: '/library',
    meta: { title: '商品库', hideInMenu: true },
  },
  {
    path: '/media',
    component: workflowComponent,
    meta: { title: '图片与文案', hideInMenu: true },
  },
  {
    path: '/pricing',
    component: workflowComponent,
    meta: { title: '核价', hideInMenu: true },
  },
  {
    path: '/publish',
    component: workflowComponent,
    meta: { title: '发布预检', hideInMenu: true },
  },
  {
    path: '/settings',
    component: workflowComponent,
    meta: { title: '设置', hideInMenu: true },
  },
  {
    path: '/auth',
    component: workflowComponent,
    meta: { title: '授权', hideInMenu: true },
  },
  {
    path: '/logs',
    component: workflowComponent,
    meta: { title: '日志', hideInMenu: true },
  },
  {
    path: '/pending',
    component: workflowComponent,
    meta: { title: '待处理', hideInMenu: true },
  },
]

const routes: RouteRecordRaw[] = [
  // Setup Routes
  // Public Routes
  ...workflowRoutes,
  // Auth Routes
  // User Routes
  ...legacyEntryRoutes,
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
