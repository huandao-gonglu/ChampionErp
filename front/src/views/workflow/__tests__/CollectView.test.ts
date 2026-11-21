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

const browser: api.CollectionLink = { id: 'a', url: 'https://example.com/item', mode: 'browser', revision: 1, platform: 'generic', status: 'pending', createdAt: null, updatedAt: '' }
const apiRow: api.CollectionLink = { ...browser, id: 'b', url: 'https://detail.1688.com/offer/1.html', mode: 'api', revision: 4, platform: '1688', status: 'failed', title: '扳手', error: '资料不完整' }

function mockViewport(initialWidth: number) {
  let width = initialWidth
  const queries: MediaQueryList[] = []
  vi.spyOn(window, 'matchMedia').mockImplementation(query => {
    const minimum = Number(query.match(/min-width: (\d+)px/)?.[1] ?? Infinity)
    const media = Object.defineProperties(new EventTarget(), {
      matches: { get: () => width >= minimum },
      media: { value: query },
    }) as MediaQueryList
    queries.push(media)
    return media
  })
  return (nextWidth: number) => {
    width = nextWidth
    for (const media of queries) media.dispatchEvent(Object.assign(new Event('change'), { matches: media.matches }))
  }
}
let resizeViewport: ReturnType<typeof mockViewport>

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) })
  vi.resetAllMocks()
  resizeViewport = mockViewport(1920)
  setActivePinia(createPinia())
  vi.mocked(api.listLinks).mockResolvedValue([browser, apiRow])
  vi.mocked(api.linkHistory).mockResolvedValue([])
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setAccountIdentity('') })

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
    expect(btn('重试失败').attributes('disabled')).toBeDefined()
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
    expect(wrapper.get<HTMLButtonElement>('dialog header button[type=submit]').element.form).toBe(wrapper.get('form').element)
    vi.mocked(api.manageLinks).mockRejectedValueOnce(new Error('第 2 个链接无效'))
    await wrapper.get('textarea').setValue(browser.url + '\njavascript:invalid')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('dialog [role=alert]').text()).toContain('第 2 个链接无效')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(browser.url + '\njavascript:invalid')
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

it.each([1366, 1440, 1920, 2560])('%i px 视口使用嵌入式详情，关闭时归还列表宽度', async width => {
  resizeViewport(width)
  const wrapper = mount(CollectView, { props: { form: createDefaultCollectForm(), loading: false, error: '' } })
  try {
    await flushPromises()
    expect(wrapper.get('.collection-panes').classes()).toContain('collection-panes--split')
    expect(wrapper.get('[aria-label="采集链接列表"]').isVisible()).toBe(true)
    expect(wrapper.get('[aria-label="链接详情"]').isVisible()).toBe(true)
    expect(wrapper.get('header').text()).toContain('开始待采集')
    await wrapper.get('[aria-label="关闭详情"]').trigger('click')
    expect(wrapper.get('.collection-panes').classes()).not.toContain('collection-panes--split')
    expect(wrapper.find('aside').exists()).toBe(false)
  } finally { wrapper.unmount() }
})

it('窄窗口返回列表时恢复筛选、排序、勾选、当前条目和滚动', async () => {
  resizeViewport(1279)
  const first = { ...apiRow, title: 'B 扳手' }
  const second = { ...apiRow, id: 'c', url: 'https://detail.1688.com/offer/2.html', title: 'A 积木', error: '' }
  vi.mocked(api.listLinks).mockResolvedValue([browser, first, second])
  const run = vi.spyOn(useWorkflowCollectionStore(), 'runLinks').mockResolvedValue(undefined)
  const wrapper = mount(CollectView, { attachTo: document.body, props: { form: createDefaultCollectForm(), loading: false, error: '' } })
  const btn = (s: string) => wrapper.findAll('button').find(button => button.text().includes(s))!
  const list = () => wrapper.get<HTMLElement>('[aria-label="采集链接列表"]')
  const titles = () => list().findAll('tbody strong').map(item => item.text())
  try {
    await flushPromises()
    expect(wrapper.find('aside').exists()).toBe(false)
    await list().get('input[type=search]').setValue('detail.1688.com')
    await btn('失败 2').trigger('click')
    await wrapper.get('[aria-label="按商品名称或 URL 排序"]').trigger('click')
    await list().get('input[aria-label="选择 B 扳手"]').setValue(true)
    expect(titles()).toEqual(['A 积木', 'B 扳手'])
    list().element.scrollTop = 240
    await btn('A 积木').trigger('click')
    await flushPromises()
    expect(list().isVisible()).toBe(false)
    expect(wrapper.get('aside a').attributes('href')).toBe(second.url)
    expect(wrapper.find('dialog').exists()).toBe(false)
    expect(document.activeElement).toBe(btn('返回列表').element)

    list().element.scrollTop = 0
    await btn('返回列表').trigger('click')
    await flushPromises()
    expect(list().isVisible()).toBe(true)
    expect(wrapper.find('aside').exists()).toBe(false)
    expect(list().get<HTMLInputElement>('input[type=search]').element.value).toBe('detail.1688.com')
    expect(btn('失败 2').attributes('aria-pressed')).toBe('true')
    expect(list().get('th[aria-sort]').attributes('aria-sort')).toBe('ascending')
    expect(titles()).toEqual(['A 积木', 'B 扳手'])
    expect(list().get<HTMLInputElement>('input[aria-label="选择 B 扳手"]').element.checked).toBe(true)
    expect(btn('A 积木').attributes('aria-pressed')).toBe('true')
    expect(list().element.scrollTop).toBe(240)
    expect(document.activeElement).toBe(btn('A 积木').element)
    await btn('重试失败（1）').trigger('click')
    expect(run).toHaveBeenCalledWith([{ id: first.id, revision: first.revision }])
  } finally { wrapper.unmount() }
})

it('跨 1280 px 切换时保留用户打开的详情', async () => {
  resizeViewport(1280)
  const wrapper = mount(CollectView, { attachTo: document.body, props: { form: createDefaultCollectForm(), loading: false, error: '' } })
  try {
    await flushPromises()
    expect(wrapper.get('.collection-panes').classes()).toContain('collection-panes--split')
    await wrapper.findAll('[data-collect-row]')[1].trigger('click')
    resizeViewport(1279)
    await flushPromises()
    expect(wrapper.get('[aria-label="采集链接列表"]').isVisible()).toBe(false)
    expect(wrapper.get('aside a').attributes('href')).toBe(apiRow.url)
    resizeViewport(1280)
    await flushPromises()
    expect(wrapper.get('.collection-panes').classes()).toContain('collection-panes--split')
    expect(wrapper.get('[aria-label="采集链接列表"]').isVisible()).toBe(true)
    expect(wrapper.get('aside a').attributes('href')).toBe(apiRow.url)
  } finally { wrapper.unmount() }
})

it.each(['browser', 'api'] as const)('%s 模式添加时提交完整分享文案并显示转换后的待采集链接', async mode => {
  const share = '【水壶】复制￥test￥，打开【手机阿里】查看：https\\://qr.1688.com/s/Example CZ1387'
  const row = { ...apiRow, mode, url: 'https://detail.1688.com/offer/826923188477.html', title: undefined, status: 'pending' as const }
  vi.mocked(api.listLinks).mockResolvedValueOnce([]).mockResolvedValue([row])
  vi.mocked(api.manageLinks).mockResolvedValue({ added: 1, duplicates: 0 })
  const wrapper = mount(CollectView, {
    props: { form: createDefaultCollectForm(), loading: false, error: '' },
    global: { stubs: { Teleport: true } },
  })
  try {
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().trim() === '添加链接')!.trigger('click')
    if (mode === 'api') await wrapper.get('dialog select').setValue('api')
    await wrapper.get('textarea').setValue(share)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenCalledWith({ action: 'add', items: [{ url: share, mode }] })
    expect(wrapper.text()).toContain(row.url)
    expect(api.runLink).not.toHaveBeenCalled()
  } finally { wrapper.unmount() }
})

it('支持顶部快速粘贴加入、详情内直接重试和勾选批量移除', async () => {
  const store = useWorkflowCollectionStore()
  const run = vi.spyOn(store, 'runLinks').mockResolvedValue(undefined)
  vi.mocked(api.manageLinks).mockResolvedValue({ added: 1, duplicates: 0 })
  const form = createDefaultCollectForm()
  const wrapper = mount(CollectView, {
    props: { form, loading: false, error: '' },
    global: { stubs: { Teleport: true } },
  })
  try {
    await flushPromises()
    const btn = (s: string) => wrapper.findAll('button').find(button => button.text().includes(s))!
    await btn('1688 API').trigger('click')
    expect(form.mode).toBe('api')
    const quickInput = wrapper.get('input[aria-label="快速粘贴商品链接或 1688 分享文案"]')
    await quickInput.setValue('https://detail.1688.com/offer/999.html')
    await quickInput.trigger('keydown.enter')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenCalledWith({
      action: 'add',
      items: [{ url: 'https://detail.1688.com/offer/999.html', mode: 'api' }],
    })
    expect((quickInput.element as HTMLInputElement).value).toBe('')

    await btn('扳手').trigger('click')
    await wrapper.get('aside').findAll('button').find(button => button.text().includes('重新采集'))!.trigger('click')
    expect(run).toHaveBeenCalledWith([{ id: apiRow.id, revision: apiRow.revision }])

    await wrapper.get('input[aria-label="全选当前筛选结果"]').setValue(true)
    await btn('批量移除').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenCalledWith({ action: 'remove', id: browser.id, revision: browser.revision })
    expect(api.manageLinks).toHaveBeenCalledWith({ action: 'remove', id: apiRow.id, revision: apiRow.revision })
  } finally { wrapper.unmount() }
})

it('默认按添加时间最新在前，支持切回最早在前、按采集方式筛选及批量改方式', async () => {
  const older: api.CollectionLink = { ...browser, id: 'old', title: '旧商品', createdAt: '2026-11-18T08:00:00+00:00' }
  const newer: api.CollectionLink = { ...apiRow, id: 'new', title: '新商品', createdAt: '2026-11-20T08:00:00+00:00' }
  vi.mocked(api.listLinks).mockResolvedValue([older, newer])
  vi.mocked(api.manageLinks).mockResolvedValue({})
  const wrapper = mount(CollectView, {
    props: { form: createDefaultCollectForm(), loading: false, error: '' },
    global: { stubs: { Teleport: true } },
  })
  const titles = () => wrapper.findAll('tbody strong').map(item => item.text())
  const btn = (s: string) => wrapper.findAll('button').find(button => button.text().includes(s))!
  try {
    await flushPromises()
    expect(titles()).toEqual(['新商品', '旧商品'])
    await wrapper.get('button[aria-label*="按添加时间排序"]').trigger('click')
    expect(titles()).toEqual(['旧商品', '新商品'])

    await wrapper.get('select[aria-label="按采集方式筛选"]').setValue('browser')
    expect(titles()).toEqual(['旧商品'])

    await wrapper.get('input[aria-label="全选当前筛选结果"]').setValue(true)
    await btn('改方式').trigger('click')
    await wrapper.get('dialog select').setValue('api')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenCalledWith({
      action: 'edit', id: older.id, revision: older.revision, url: older.url, mode: 'api',
    })
  } finally { wrapper.unmount() }
})

it('批量移除仅作用于当前筛选中可见的勾选项，不会误删隐藏项', async () => {
  vi.mocked(api.manageLinks).mockResolvedValue({})
  const wrapper = mount(CollectView, {
    props: { form: createDefaultCollectForm(), loading: false, error: '' },
    global: { stubs: { Teleport: true } },
  })
  const btn = (s: string) => wrapper.findAll('button').find(button => button.text().includes(s))!
  try {
    await flushPromises()
    await wrapper.get('input[aria-label="全选当前筛选结果"]').setValue(true)
    await wrapper.get('select[aria-label="按采集方式筛选"]').setValue('api')
    await btn('批量移除').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.manageLinks).toHaveBeenCalledTimes(1)
    expect(api.manageLinks).toHaveBeenCalledWith({ action: 'remove', id: apiRow.id, revision: apiRow.revision })
  } finally { wrapper.unmount() }
})

it('批量采集中调用 stopRun 后在当前条完成后停止后续队列，并维护批次进度', async () => {
  const first: api.CollectionLink = { ...apiRow, id: 'p1', revision: 1, status: 'pending' }
  const second: api.CollectionLink = { ...apiRow, id: 'p2', revision: 2, status: 'pending', url: 'https://detail.1688.com/offer/2.html' }
  vi.mocked(api.listLinks).mockResolvedValue([first, second])
  const store = useWorkflowCollectionStore()
  const observed: Array<{ current: number; total: number; status: string }> = []
  vi.mocked(api.runLink).mockImplementation(async (target) => {
    const localRow = store.links.find(item => item.id === target.id)!
    observed.push({ current: store.runProgress!.current, total: store.runProgress!.total, status: localRow.status })
    store.stopRun()
    return { ...first, revision: 2, status: 'success' }
  })
  await store.runLinks([first, second])
  expect(observed).toEqual([{ current: 1, total: 2, status: 'running' }])
  expect(api.runLink).toHaveBeenCalledTimes(1)
  expect(store.runProgress).toBeNull()
  expect(store.running).toBe(false)
})
