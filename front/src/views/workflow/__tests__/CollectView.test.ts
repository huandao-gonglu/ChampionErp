import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CollectView from '../CollectView.vue'
import { createDefaultCollectForm } from '@/constants/initialState'
import { launchDesktop } from '@/utils/desktopHandoff'
import * as api from '@/api/workflow/collectionLinks'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { setAccountIdentity, businessStorageKey } from '@/utils/accountTransport'
import { COLLECT_QUEUE_STORAGE_KEY } from '@/utils/collectQueue'

vi.mock('@/api/workflow/collectionLinks', () => ({
  listLinks: vi.fn(), manageLinks: vi.fn(), runLink: vi.fn(), verifyLink: vi.fn(), linkHistory: vi.fn(),
}))
vi.mock('@/utils/desktopHandoff', () => ({ launchDesktop: vi.fn() }))

const browser: api.CollectionLink = { id: 'a', url: 'https://example.com/item', mode: 'browser', revision: 1, platform: 'generic', status: 'pending', updatedAt: '' }
const apiRow: api.CollectionLink = { ...browser, id: 'b', url: 'https://detail.1688.com/offer/1.html', mode: 'api', revision: 4, platform: '1688', status: 'failed', title: '扳手', error: '资料不完整' }

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) })
  vi.resetAllMocks()
  setActivePinia(createPinia())
  vi.mocked(api.listLinks).mockResolvedValue([browser, apiRow])
  vi.mocked(api.linkHistory).mockResolvedValue([])
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setAccountIdentity('') })

it('切换链接时结果与日志归属于所选 URL，API 直接执行，浏览器只交接指定版本', async () => {
  const store = useWorkflowCollectionStore()
  const run = vi.spyOn(store, 'runLinks').mockResolvedValue(undefined)
  const wrapper = mount(CollectView, { props: { form: createDefaultCollectForm(), loading: false, error: '' } })
  try {
    await flushPromises()
    const btn = (s: string) => wrapper.findAll('button').find(b => b.text().includes(s))!
    await btn('开始待采集').trigger('click')
    expect(launchDesktop).toHaveBeenCalledWith(expect.objectContaining({ state: { workflow: { collectAction: { type: 'batch', targets: [{ id: 'a', revision: 1 }] } } } }))
    await btn('扳手').trigger('click')
    expect(wrapper.get('aside').text()).toContain('资料不完整')
    await btn('诊断日志').trigger('click')
    expect(api.linkHistory).toHaveBeenCalledWith('b')
    await btn('重试失败').trigger('click')
    expect(run).toHaveBeenCalledWith([{ id: 'b', revision: 4 }])
    await wrapper.get('input[type=search]').setValue('example.com')
    expect(wrapper.get('aside').text()).not.toContain('资料不完整')
    expect(wrapper.get('aside a').attributes('href')).toBe(browser.url)
  } finally { wrapper.unmount() }
})

it('旧队列仅在后端确认导入后移除；中断的旧任务不自动重放', async () => {
  const store = useWorkflowCollectionStore()
  setAccountIdentity('owner')
  const key = businessStorageKey(COLLECT_QUEUE_STORAGE_KEY)
  localStorage.setItem(COLLECT_QUEUE_STORAGE_KEY + ':account:other', '其他账号的旧队列')
  localStorage.setItem(key, JSON.stringify([{ url: browser.url, status: 'running' }]))
  vi.mocked(api.manageLinks).mockRejectedValueOnce(new Error('暂时离线')).mockResolvedValue({})
  await store.refreshLinks()
  expect(store.syncError).toContain('暂时离线')
  expect(store.links).toEqual([browser, apiRow])
  expect(localStorage.getItem(key)).toBeTruthy()
  await store.refreshLinks()
  expect(api.manageLinks).toHaveBeenCalledWith({ action: 'import', items: [expect.objectContaining({ url: browser.url, status: 'unconfirmed' })] })
  expect(localStorage.getItem(key)).toBeNull()
  expect(localStorage.getItem(COLLECT_QUEUE_STORAGE_KEY + ':account:other')).toBe('其他账号的旧队列')
  expect(api.runLink).not.toHaveBeenCalled()
})

it('人工验证后按后台新版本继续，跳过已在其他窗口修改的目标', async () => {
  vi.useFakeTimers()
  const waiting = { ...browser, revision: 3, status: 'waiting_verification' as const }
  vi.mocked(api.runLink).mockResolvedValueOnce(waiting).mockResolvedValueOnce({ ...browser, revision: 5, status: 'success' })
  vi.mocked(api.verifyLink).mockResolvedValueOnce('waiting_verification').mockResolvedValueOnce('ready')
  const store = useWorkflowCollectionStore()
  const run = store.runLinks([browser, { id: apiRow.id, revision: 1 }])
  await vi.advanceTimersByTimeAsync(3000)
  await run
  expect(api.runLink).toHaveBeenCalledTimes(2)
  expect(api.runLink).toHaveBeenLastCalledWith(waiting)
  expect(api.verifyLink).toHaveBeenCalledTimes(2)
})

it('取消等待后保留原验证项及未启动的后续链接', async () => {
  vi.useFakeTimers()
  const waiting = { ...browser, status: 'waiting_verification' as const }
  vi.mocked(api.listLinks).mockResolvedValue([waiting, apiRow])
  const store = useWorkflowCollectionStore()
  const run = store.runLinks([waiting, apiRow])
  await vi.advanceTimersByTimeAsync(1)
  store.cancelWait()
  await vi.advanceTimersByTimeAsync(1500)
  await run
  expect(api.runLink).not.toHaveBeenCalled()
  expect(store.links[0].status).toBe('waiting_verification')
})

it('添加与编辑等待写入回执，错误时保留输入；移除操作只提交选中行版本', async () => {
  const wrapper = mount(CollectView, {
    props: { form: createDefaultCollectForm(), loading: false, error: '' },
    global: { stubs: { Teleport: true } },
  })
  try {
    await flushPromises()
    const btn = (s: string) => wrapper.findAll('button').find(b => b.text().trim() === s)!
    await btn('添加链接').trigger('click')
    await wrapper.get('textarea').setValue(browser.url + '\njavascript:invalid')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('dialog [role=alert]').text()).toContain('第 2 个链接无效')
    expect(api.manageLinks).not.toHaveBeenCalled()
    await wrapper.get('textarea').setValue(browser.url)
    vi.mocked(api.manageLinks).mockRejectedValueOnce(new Error('保存失败')).mockResolvedValueOnce({ added: 0, duplicates: 1 })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('dialog [role=alert]').text()).toBe('保存失败')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(browser.url)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('dialog').exists()).toBe(false)
    expect(wrapper.text()).toContain('跳过 1 条重复链接')
    await wrapper.get('[aria-label="编辑链接"]').trigger('click')
    await wrapper.get('input[type=url]').setValue('https://example.test/updated')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenLastCalledWith({ action: 'edit', id: browser.id, revision: 1, mode: 'browser', url: 'https://example.test/updated' })
    await wrapper.get('[aria-label="移除链接"]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenLastCalledWith({ action: 'remove', id: browser.id, revision: 1 })
  } finally { wrapper.unmount() }
})
