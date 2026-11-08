import { createRouter, createWebHistory, type LocationQueryRaw, type RouteRecordRaw } from 'vue-router'
import { updateDocumentTitle } from './title'
import { useAccountStore } from '@/stores/account'
import { accountDestination } from '@/utils/accountTransport'

const workflowComponent = () => import('@/views/workflow/WorkflowView.vue')

const workflowRoutes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'WorkflowHome',
    component: workflowComponent,
    meta: {
      title: '跨境 ERP Web 工作台',
      description: '采集、采集箱、编辑、图片文案、核价、预检、授权和日志。',
      icon: '▦',
      keepAlive: true,
    },
  },
]

const legacyWorkflowEntries = [
  { path: '/collect', tab: 'collect', title: '采集' },
  { path: '/research', tab: 'research', title: '选品调研' },
  { path: '/library', tab: 'library', title: '采集箱' },
  { path: '/drafts', tab: 'drafts', title: '草稿箱' },
  { path: '/online-products', tab: 'onlineProducts', title: '在线商品' },
  { path: '/edit', tab: 'library', title: '采集箱' },
  { path: '/media', tab: 'library', title: '采集箱' },
  { path: '/pricing', tab: 'drafts', title: '草稿箱' },
  { path: '/publish', tab: 'publish', title: '任务队列' },
  { path: '/settings', tab: 'systemSettings', title: '设置' },
  { path: '/auth', tab: 'auth', title: '授权' },
] as const

const legacyEntryRoutes: RouteRecordRaw[] = legacyWorkflowEntries.map((entry) => ({
  path: entry.path,
  redirect: (to) => {
    const query: LocationQueryRaw = { ...to.query, tab: String(to.query.tab || entry.tab) }
    if (entry.path === '/settings' && to.query.auth_section) {
      query.tab = 'auth'
    }
    return { name: 'WorkflowHome', query }
  },
  meta: { title: entry.title, hideInMenu: true },
}))

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'AccountLogin', component: () => import('@/views/account/AccountLoginView.vue'), meta: { public: true, title: '登录' } },
  { path: '/register', name: 'AccountRegister', component: () => import('@/views/account/AccountLoginView.vue'), meta: { public: true, title: '注册' } },
  { path: '/account', name: 'PersonalAccount', component: () => import('@/views/account/PersonalAccountView.vue'), meta: { title: '个人账号' } },
  { path: '/admin/users', name: 'AccountUsers', component: () => import('@/views/account/AccountUsersView.vue'), meta: { admin: true, title: '用户管理' } },
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
      title: 'AI 对话',
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
      title: '页面不存在',
      description: '请求的页面不存在或已迁移。',
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

router.beforeEach(async (to) => {
  const account = useAccountStore()
  if (!account.ready) {
    try { await account.refresh() } catch { /* 登录页展示连接错误，业务页继续受身份保护。 */ }
  }
  if (!to.meta.public && !account.user) return { name: 'AccountLogin', query: { redirect: to.fullPath } }
  if (to.meta.admin && !account.isAdmin) return { name: 'WorkflowHome' }
  if (to.meta.public && account.user) return accountDestination(to.query.redirect)
  if (to.name === 'WorkflowHome' && to.query.auth_section === 'interruptions') {
    const query: LocationQueryRaw = { ...to.query, tab: 'systemSettings', settings_section: 'interruptions' }
    delete query.auth_section
    return { name: 'WorkflowHome', query, hash: to.hash, replace: true }
  }
  if (to.name === 'WorkflowHome' && (to.query.settings_section === 'logistics' || to.query.auth_section === 'logistics')) {
    const query: LocationQueryRaw = { ...to.query, tab: 'auth', auth_section: 'crossborderbus', bus_section: 'rules' }
    delete query.settings_section
    return { name: 'WorkflowHome', query, hash: to.hash, replace: true }
  }
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
