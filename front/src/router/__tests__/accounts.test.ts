import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { router } from '../index'
import { useAccountStore } from '@/stores/account'
import { accountDestination, accountFetch, businessStorageKey, clearBusinessStorage, setAccountIdentity, setCsrfToken } from '@/utils/accountTransport'

beforeEach(() => {
  setActivePinia(createPinia())
  useAccountStore().ready = true
  setCsrfToken('')
  setAccountIdentity('')
  Object.defineProperty(window, 'scrollTo', { configurable: true, value: () => undefined })
})
afterEach(() => { vi.unstubAllGlobals(); setAccountIdentity('') })

describe('账号路由及传输边界', () => {
  it('未登录业务页转到登录并保留站内目的地址，登录和注册可公开访问', async () => {
    await router.push('/?tab=drafts')
    expect(router.currentRoute.value.name).toBe('AccountLogin')
    expect(router.currentRoute.value.query.redirect).toBe('/?tab=drafts')
    await router.push('/register')
    expect(router.currentRoute.value.name).toBe('AccountRegister')
  })
  it('普通用户无法进入管理页面，管理员可以进入', async () => {
    const store = useAccountStore()
    store.user = { id: 'a', email: 'a@example.com', display_name: '甲', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 }
    await router.push('/admin/users')
    expect(router.currentRoute.value.name).toBe('WorkflowHome')
    store.user.role = 'admin'
    await router.push('/admin/users')
    expect(router.currentRoute.value.name).toBe('AccountUsers')
  })
  it('AI 流携带会话和当前 CSRF', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    setCsrfToken('csrf-test')
    await accountFetch('/api/v1/ai-chat/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    const init = fetch.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-test')
    vi.unstubAllGlobals()
  })
  it('清理账号业务缓存并保留主题和语言', () => {
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      get length() { return values.size },
      key: (index: number) => [...values.keys()][index] || null,
      getItem: (key: string) => values.get(key) || null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    })
    localStorage.setItem('ai-chat-selected-conversation', 'old')
    localStorage.setItem('champion-erp.collect-queue.v1', 'old')
    localStorage.setItem('draft-image-request:old', 'old')
    localStorage.setItem('theme', 'dark')
    localStorage.setItem('uiLocale', 'zh-CN')
    clearBusinessStorage()
    expect(localStorage.getItem('ai-chat-selected-conversation')).toBeNull()
    expect(localStorage.getItem('champion-erp.collect-queue.v1')).toBeNull()
    expect(localStorage.getItem('draft-image-request:old')).toBeNull()
    expect(localStorage.getItem('theme')).toBe('dark')
    expect(localStorage.getItem('uiLocale')).toBe('zh-CN')
  })
  it('不同账号使用不同的业务缓存键', () => {
    setAccountIdentity('a')
    const first = businessStorageKey('ai-chat-selected-conversation')
    setAccountIdentity('b')
    expect(businessStorageKey('ai-chat-selected-conversation')).not.toBe(first)
  })
  it('登录后目的地址只允许站内路径', () => {
    for (const path of ['https://elsewhere.test', '//elsewhere.test', '/\\elsewhere.test', '/login', '/register?x=1']) expect(accountDestination(path)).toBe('/')
    expect(accountDestination('/?tab=drafts')).toBe('/?tab=drafts')
  })
})
