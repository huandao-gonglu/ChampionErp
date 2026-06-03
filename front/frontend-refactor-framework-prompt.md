# 前端项目框架与布局重构提示词

> 用途：将本文档内容复制给另一个项目中的 AI，让它参考本项目的前端架构，对目标项目进行更规范的前端重构。

---

## 一、重构目标

请参考下面这套前端工程架构，对当前项目的前端进行规范化重构。

目标不是简单照搬业务代码，而是按照该架构重新梳理：

- 目录结构
- 职责边界
- 路由设计
- 状态管理
- API 请求层
- 组件分层
- 布局系统
- 样式规范
- 类型定义
- 工具函数
- 国际化
- 测试体系

使目标项目更符合现代前端开发规范，并具备更好的可维护性、可扩展性和工程一致性。

---

## 二、推荐技术栈

请尽量采用或对齐以下技术栈：

- Vue 3
- TypeScript
- Vite
- Pinia
- Vue Router
- Axios
- vue-i18n
- Tailwind CSS
- Vitest + Vue Test Utils
- ESLint
- vue-tsc

如果目标项目不是 Vue 技术栈，请将以下架构思想映射到对应框架中，例如：

- React 中用 Zustand / Redux / Jotai 替代 Pinia
- React Router 替代 Vue Router
- hooks 替代 composables
- pages / views + components + services / api 保持同样分层

---

## 三、推荐目录结构

请将前端整理为如下结构：

```txt
frontend/
├── index.html
├── package.json
├── vite.config.ts
├── vitest.config.ts
├── tsconfig.json
├── tailwind.config.js
├── postcss.config.js
├── .eslintrc.cjs
└── src/
    ├── main.ts
    ├── App.vue
    ├── style.css
    │
    ├── api/
    │   ├── client.ts
    │   ├── index.ts
    │   ├── auth.ts
    │   ├── user.ts
    │   ├── payment.ts
    │   ├── usage.ts
    │   ├── setup.ts
    │   └── admin/
    │       ├── index.ts
    │       ├── users.ts
    │       ├── settings.ts
    │       ├── dashboard.ts
    │       └── ...
    │
    ├── router/
    │   ├── index.ts
    │   ├── meta.d.ts
    │   ├── title.ts
    │   └── setupRedirect.ts
    │
    ├── stores/
    │   ├── index.ts
    │   ├── app.ts
    │   ├── auth.ts
    │   ├── subscriptions.ts
    │   ├── announcements.ts
    │   └── ...
    │
    ├── views/
    │   ├── HomeView.vue
    │   ├── NotFoundView.vue
    │   ├── auth/
    │   ├── user/
    │   ├── admin/
    │   ├── public/
    │   ├── setup/
    │   └── tools/
    │
    ├── components/
    │   ├── layout/
    │   ├── common/
    │   ├── auth/
    │   ├── user/
    │   ├── admin/
    │   ├── icons/
    │   └── domain-specific/
    │
    ├── composables/
    │   ├── useForm.ts
    │   ├── useTableLoader.ts
    │   ├── useAutoRefresh.ts
    │   ├── useClipboard.ts
    │   └── ...
    │
    ├── constants/
    │   ├── branding.ts
    │   └── ...
    │
    ├── types/
    │   ├── index.ts
    │   ├── global.d.ts
    │   └── ...
    │
    ├── utils/
    │   ├── format.ts
    │   ├── apiError.ts
    │   ├── sanitize.ts
    │   ├── url.ts
    │   └── ...
    │
    ├── i18n/
    │   ├── index.ts
    │   └── locales/
    │       ├── zh.ts
    │       └── en.ts
    │
    ├── styles/
    │   └── ...
    │
    └── __tests__/
        └── setup.ts
```

---

## 四、入口层设计

### `src/main.ts`

职责：

1. 创建 Vue 应用。
2. 安装 Pinia。
3. 初始化全局主题，例如 dark mode。
4. 初始化全局公开配置。
5. 初始化 i18n。
6. 安装 router。
7. 等待 `router.isReady()` 后再挂载，避免首屏空白或竞态问题。

参考结构：

```ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import i18n, { initI18n } from './i18n'
import './style.css'

function initThemeClass() {
  const savedTheme = localStorage.getItem('theme')
  const shouldUseDark =
    savedTheme === 'dark' ||
    (!savedTheme && window.matchMedia('(prefers-color-scheme: dark)').matches)

  document.documentElement.classList.toggle('dark', shouldUseDark)
}

async function bootstrap() {
  initThemeClass()

  const app = createApp(App)
  const pinia = createPinia()

  app.use(pinia)

  await initI18n()

  app.use(router)
  app.use(i18n)

  await router.isReady()
  app.mount('#app')
}

bootstrap()
```

### `src/App.vue`

职责：

- 只放全局壳层。
- 渲染 `RouterView`。
- 挂载全局组件，例如：
  - 页面顶部进度条
  - Toast
  - 全局弹窗
  - 全局公告
- 不在这里写具体页面业务。

参考结构：

```vue
<script setup lang="ts">
import { RouterView } from 'vue-router'
import Toast from '@/components/common/Toast.vue'
import NavigationProgress from '@/components/common/NavigationProgress.vue'
</script>

<template>
  <NavigationProgress />
  <RouterView />
  <Toast />
</template>
```

---

## 五、布局系统

布局组件集中放在：

```txt
src/components/layout/
```

推荐包含：

```txt
layout/
├── AppLayout.vue
├── AppHeader.vue
├── AppSidebar.vue
├── AuthLayout.vue
├── TablePageLayout.vue
└── index.ts
```

### 1. `AppLayout.vue`

用于登录后的主应用布局。

职责：

- 左侧 Sidebar
- 顶部 Header
- 主内容区域
- 响应式侧边栏
- 背景装饰
- 页面主体 padding

参考结构：

```vue
<template>
  <div class="min-h-screen bg-gray-50 dark:bg-dark-950">
    <AppSidebar />

    <div
      class="relative min-h-screen transition-all duration-300"
      :class="sidebarCollapsed ? 'lg:ml-[72px]' : 'lg:ml-64'"
    >
      <AppHeader />

      <main class="p-4 md:p-6 lg:p-8">
        <slot />
      </main>
    </div>
  </div>
</template>
```

### 2. `AppHeader.vue`

职责：

- 显示当前页面标题、描述。
- 从 route meta 中读取 `titleKey`、`descriptionKey`。
- 放置用户菜单、退出登录、语言切换、主题切换、通知等。
- 移动端负责打开侧边栏。

### 3. `AppSidebar.vue`

职责：

- 管理主导航菜单。
- 根据用户角色显示不同菜单。
- 支持折叠、移动端抽屉。
- 支持 active 状态。
- 支持功能开关控制菜单显示。
- 菜单项不要硬编码散落在页面里，应集中维护。

### 4. `AuthLayout.vue`

用于登录、注册、找回密码等页面。

职责：

- 居中卡片布局。
- 统一品牌 logo、站点名称、副标题。
- 背景渐变、装饰元素。
- 提供 footer slot。

参考结构：

```vue
<template>
  <div class="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
    <div class="absolute inset-0 bg-gradient-to-br from-gray-50 via-primary-50/30 to-gray-100 dark:from-dark-950 dark:via-dark-900 dark:to-dark-950"></div>

    <div class="relative z-10 w-full max-w-md">
      <div class="mb-8 text-center">
        <!-- logo / site name / subtitle -->
      </div>

      <div class="card-glass rounded-2xl p-8 shadow-glass">
        <slot />
      </div>

      <div class="mt-6 text-center text-sm">
        <slot name="footer" />
      </div>
    </div>
  </div>
</template>
```

### 5. `TablePageLayout.vue`

用于管理后台表格类页面。

推荐 slot：

```vue
<TablePageLayout>
  <template #actions>
    <!-- 操作按钮 -->
  </template>

  <template #filters>
    <!-- 搜索 / 筛选 -->
  </template>

  <template #table>
    <!-- DataTable -->
  </template>

  <template #pagination>
    <!-- Pagination -->
  </template>
</TablePageLayout>
```

---

## 六、路由设计

路由集中在：

```txt
src/router/index.ts
```

需要按模块分组：

```ts
// Setup Routes
// Public Routes
// Auth Routes
// User Routes
// Admin Routes
// 404 Routes
```

每个路由必须使用懒加载：

```ts
{
  path: '/dashboard',
  name: 'Dashboard',
  component: () => import('@/views/user/DashboardView.vue'),
  meta: {
    requiresAuth: true,
    requiresAdmin: false,
    title: 'Dashboard',
    titleKey: 'dashboard.title',
    descriptionKey: 'dashboard.description'
  }
}
```

### Route Meta 规范

在 `src/router/meta.d.ts` 中扩展类型：

```ts
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
```

### 路由守卫要求

`router.beforeEach` 中处理：

1. 初始化登录态。
2. 判断是否需要登录。
3. 判断是否需要管理员权限。
4. 未登录跳转 `/login?redirect=xxx`。
5. 已登录访问 login / register 时跳转 dashboard。
6. 设置页面标题。
7. 根据功能开关限制部分页面访问。

`router.afterEach` 中处理：

1. 关闭导航 loading。
2. 路由预加载。
3. 滚动位置恢复。

`router.onError` 中处理：

- 动态 import chunk 加载失败时，自动刷新页面一次，避免发版后白屏。

---

## 七、状态管理 Pinia

状态统一放在：

```txt
src/stores/
```

推荐结构：

```txt
stores/
├── index.ts
├── app.ts
├── auth.ts
├── user.ts
├── settings.ts
└── ...
```

### `stores/app.ts`

管理全局 UI 状态：

- sidebar 是否折叠
- mobile sidebar 是否打开
- 全局 loading
- toast 列表
- 站点公开配置
- 主题、版本、全局设置缓存

### `stores/auth.ts`

管理认证状态：

- 当前用户
- access token
- refresh token
- token 过期时间
- 登录
- 注册
- 登出
- 刷新用户信息
- token 自动刷新
- 从 localStorage 恢复登录态

### `stores/index.ts`

统一导出：

```ts
export { useAuthStore } from './auth'
export { useAppStore } from './app'
export { useUserStore } from './user'
```

要求：

- store 使用组合式写法。
- state 用 `ref`。
- 派生状态用 `computed`。
- action 保持业务清晰。
- 不要把 API 请求散落在组件里，复杂请求应通过 store 或 api 层封装。

---

## 八、API 层设计

API 集中放在：

```txt
src/api/
```

### `api/client.ts`

封装 Axios 实例：

- 设置 baseURL。
- 设置 timeout。
- 自动携带 token。
- 自动携带语言。
- 自动处理统一响应格式。
- 自动处理 401。
- 支持 refresh token。
- 网络错误转成统一结构。

参考结构：

```ts
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})
```

请求拦截器要求：

- 加 `Authorization: Bearer <token>`。
- 加 `Accept-Language`。
- GET 请求可附加 timezone。

响应拦截器要求：

- 后端返回 `{ code, message, data }` 时自动解包 `data`。
- `code !== 0` 时抛出统一错误。
- 401 时尝试刷新 token。
- refresh 失败清理本地登录态并跳转登录页。

### API 模块拆分

按业务拆分：

```txt
api/
├── auth.ts
├── user.ts
├── payment.ts
├── usage.ts
├── admin/
│   ├── users.ts
│   ├── settings.ts
│   ├── dashboard.ts
│   └── index.ts
└── index.ts
```

要求：

- 每个业务模块只负责接口调用。
- 类型从 `types/` 引入。
- 不在组件中直接写 axios。
- `api/index.ts` 统一导出所有 API。

---

## 九、页面 views 分层

页面放在：

```txt
src/views/
```

按访问场景拆分：

```txt
views/
├── HomeView.vue
├── NotFoundView.vue
├── auth/
│   ├── LoginView.vue
│   ├── RegisterView.vue
│   └── ...
├── user/
│   ├── DashboardView.vue
│   ├── ProfileView.vue
│   └── ...
├── admin/
│   ├── DashboardView.vue
│   ├── UsersView.vue
│   ├── SettingsView.vue
│   └── ...
├── public/
└── setup/
```

页面职责：

- 页面只负责组合组件、加载页面级数据、处理页面级交互。
- 可复用 UI 下沉到 `components/`。
- 复杂逻辑下沉到 `composables/`。
- 接口调用通过 `api/` 或 `stores/`。
- 页面不要承担过多底层逻辑。

---

## 十、组件分层

组件放在：

```txt
src/components/
```

建议分层：

```txt
components/
├── common/
├── layout/
├── auth/
├── user/
├── admin/
├── icons/
└── domain-specific/
```

### `components/common/`

放通用基础组件：

```txt
Button
Input
Select
TextArea
BaseDialog
ConfirmDialog
DataTable
Pagination
Toast
LoadingSpinner
EmptyState
StatusBadge
SearchInput
DateRangePicker
StatCard
Skeleton
```

要求：

- 通用组件不依赖具体业务。
- Props 类型清晰。
- 支持 dark mode。
- 支持 slot。
- 命名统一。

### `components/layout/`

放布局组件：

- AppLayout
- AppHeader
- AppSidebar
- AuthLayout
- TablePageLayout

### `components/auth/`

放登录注册相关组件：

- OAuth 按钮
- 2FA 弹窗
- 登录协议
- 第三方登录 section

### `components/user/`

放用户端业务组件。

### `components/admin/`

放管理端业务组件。

---

## 十一、Composables 设计

复用逻辑放在：

```txt
src/composables/
```

例如：

```txt
useForm.ts
useAutoRefresh.ts
useClipboard.ts
useTableLoader.ts
useTableSelection.ts
usePersistedPageSize.ts
useRoutePrefetch.ts
useNavigationLoading.ts
```

要求：

- 组件中重复出现的逻辑必须抽成 composable。
- composable 只处理逻辑，不直接渲染 UI。
- 返回明确的 state 和 methods。
- 文件命名统一为 `useXxx.ts`。

---

## 十二、类型、常量、工具函数

### `src/types/`

集中放全局类型：

```txt
types/
├── index.ts
├── user.ts
├── payment.ts
└── ...
```

要求：

- API response 类型、实体类型、表单类型统一定义。
- 避免组件内大量重复 interface。

### `src/constants/`

放常量：

```txt
constants/
├── branding.ts
├── route.ts
├── featureFlags.ts
└── ...
```

### `src/utils/`

放纯工具函数：

```txt
utils/
├── format.ts
├── apiError.ts
├── sanitize.ts
├── url.ts
├── pricing.ts
└── ...
```

要求：

- 工具函数应是纯函数。
- 不依赖 Vue 实例。
- 可单元测试。

---

## 十三、国际化 i18n

目录：

```txt
src/i18n/
├── index.ts
└── locales/
    ├── zh.ts
    └── en.ts
```

要求：

- 使用 `vue-i18n`。
- 默认语言从 localStorage 或浏览器语言判断。
- 支持动态加载语言包。
- 路由标题使用 `titleKey`。
- 组件中使用 `t('xxx')`，不要硬编码大量中文或英文。
- 切换语言时同步更新 document title。

---

## 十四、样式系统

使用 Tailwind CSS。

### `tailwind.config.js`

需要定义：

- primary 主色
- accent 辅助色
- dark 深色模式颜色
- 字体
- 阴影
- 动画
- 圆角
- 背景渐变

### `src/style.css`

使用 Tailwind layers：

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-accent-50 text-accent-950 dark:bg-dark-950 dark:text-gray-100;
  }
}

@layer components {
  .btn {
    @apply inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium;
  }

  .btn-primary {
    @apply bg-gradient-to-r from-primary-500 to-primary-600 text-white;
  }

  .btn-secondary {
    @apply bg-white dark:bg-dark-900 text-gray-700 dark:text-gray-200 border border-gray-200 dark:border-dark-600;
  }

  .input {
    @apply w-full rounded-xl px-4 py-2.5 text-sm bg-white dark:bg-dark-900 border border-accent-200 dark:border-dark-700;
  }

  .card {
    @apply bg-white dark:bg-dark-900/70 rounded-2xl border border-accent-200/80 dark:border-dark-700/70 shadow-card;
  }
}
```

要求：

- 常用按钮、输入框、卡片、弹窗、表格样式沉淀为 class。
- 页面中避免大量重复 Tailwind 组合。
- 全站支持 dark mode，使用 `darkMode: 'class'`。
- 主题切换通过修改 `document.documentElement.classList`。

---

## 十五、测试规范

使用 Vitest。

目录规范：

```txt
src/**/__tests__/*.spec.ts
src/**/*.spec.ts
src/**/*.test.ts
```

测试重点：

- common 组件
- composables
- stores
- utils
- 关键页面
- API 错误处理
- 路由守卫

`vitest.config.ts` 中配置：

- jsdom
- setupFiles
- coverage
- alias `@ -> src`

---

## 十六、工程配置建议

### `package.json` scripts

建议提供：

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext .vue,.js,.jsx,.cjs,.mjs,.ts,.tsx,.cts,.mts --fix",
    "lint:check": "eslint . --ext .vue,.js,.jsx,.cjs,.mjs,.ts,.tsx,.cts,.mts",
    "typecheck": "vue-tsc --noEmit",
    "test": "vitest",
    "test:run": "vitest run",
    "test:coverage": "vitest run --coverage"
  }
}
```

### `vite.config.ts`

要求：

- 使用 `@vitejs/plugin-vue`。
- 配置 alias：`@ -> src`。
- 配置 dev server proxy。
- 配置 build output。
- 可按 vendor 分包。

### `tsconfig.json`

要求：

- 开启 `strict`。
- 配置 `@/* -> ./src/*`。
- 使用 `moduleResolution: bundler`。
- 包含 `.vue`、`.ts`、`.tsx` 文件。

---

## 十七、重构执行原则

请按以下步骤重构当前项目：

1. 先分析当前项目现有目录、技术栈和业务模块。
2. 保留已有业务功能和接口行为，不随意删除功能。
3. 先建立标准目录结构。
4. 将入口、路由、状态管理、API 层拆出来。
5. 将页面迁移到 `views/`。
6. 将复用组件迁移到 `components/common/`。
7. 将布局组件迁移到 `components/layout/`。
8. 将认证相关组件迁移到 `components/auth/`。
9. 将用户端业务组件迁移到 `components/user/`。
10. 将管理端业务组件迁移到 `components/admin/`。
11. 将重复逻辑抽成 `composables/`。
12. 将类型集中到 `types/`。
13. 将常量集中到 `constants/`。
14. 将工具函数集中到 `utils/`。
15. 增加或修复 ESLint、TypeScript、Vitest 配置。
16. 最后运行校验命令。

建议最终运行：

```bash
pnpm lint:check
pnpm typecheck
pnpm test:run
pnpm build
```

---

## 十八、最终交付要求

请输出：

1. 重构后的目录树。
2. 每个目录的职责说明。
3. 关键文件说明：
   - `main.ts`
   - `App.vue`
   - `router/index.ts`
   - `api/client.ts`
   - `stores/app.ts`
   - `stores/auth.ts`
   - `components/layout/*`
4. 说明哪些代码被移动、拆分、合并。
5. 说明仍需人工确认的业务点。
6. 确保项目可以正常启动、构建和测试。

---

## 十九、核心架构总结

这套前端架构的核心是：

```txt
Vue3 + Vite + TypeScript + Pinia + Vue Router + Axios + Tailwind CSS + i18n + Vitest
```

并通过以下分层保证可维护性：

```txt
入口层 main/App
  ↓
路由层 router
  ↓
布局层 layout
  ↓
页面层 views
  ↓
业务组件 components/domain
  ↓
通用组件 components/common
  ↓
状态层 stores
  ↓
请求层 api
  ↓
工具层 utils / constants / types / composables
```

重构时应重点保证：

- 页面和组件分离
- UI 和业务逻辑分离
- API 调用集中管理
- 状态集中管理
- 类型集中定义
- 通用能力可复用
- 样式系统统一
- 路由权限清晰
- 支持暗色模式和国际化
- 支持测试、类型检查和构建校验
