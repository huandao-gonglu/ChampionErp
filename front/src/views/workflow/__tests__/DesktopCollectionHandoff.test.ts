import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as handoffApi from '@/api/desktopHandoff'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowCollectionStore } from '@/stores/workflow/collection'
import { receivedDesktopHandoff, type DesktopWorkflowState } from '@/utils/desktopHandoff'
import WorkflowView from '../WorkflowView.vue'

beforeEach(() => {
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }))
  vi.spyOn(handoffApi, 'confirmDesktopHandoff').mockResolvedValue(undefined)
  vi.stubGlobal('championDesktop', { localOperations: true })
})
afterEach(() => {
  receivedDesktopHandoff.value = null
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function setup(state: DesktopWorkflowState, busy = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const collection = useWorkflowCollectionStore()
  const activity = useWorkflowActivityStore()
  activity.loading = busy
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  const single = vi.spyOn(workflow, 'collectProduct').mockResolvedValue(undefined)
  const batch = vi.spyOn(collection, 'runLinks').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'WorkflowHome', component: WorkflowView },
  ] })
  await router.push('/?tab=collect')
  receivedDesktopHandoff.value = { requestId: 'collect-request', target: '/?tab=collect', state: { workflow: state } }
  const wrapper = shallowMount(WorkflowView, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, workflow, collection, activity, single, batch }
}

it('恢复链接后直接开始单链接采集，并确认已接收', async () => {
  const collect = { mode: 'browser', productUrl: 'https://detail.1688.com/offer/1.html', platform: '1688' }
  const { wrapper, collection, single, batch, activity } = await setup({ collect, collectAction: { type: 'single' } })
  try {
    expect(collection.collectForm).toMatchObject(collect)
    expect(handoffApi.confirmDesktopHandoff).toHaveBeenCalledWith('collect-request')
    expect(single).toHaveBeenCalledOnce()
    expect(batch).not.toHaveBeenCalled()
    expect(receivedDesktopHandoff.value).toBeNull()
    activity.loading = true
    await flushPromises()
    activity.loading = false
    await flushPromises()
    expect(single).toHaveBeenCalledOnce()
  } finally { wrapper.unmount() }
})

it('已打开的客户端忙碌时等待，空闲后仅采集网页指定的重试项', async () => {
  const targets = [{ id: 'link-to-retry', revision: 4 }]
  const { wrapper, activity, single, batch } = await setup({
    collectAction: { type: 'batch', targets },
  }, true)
  try {
    expect(batch).not.toHaveBeenCalled()
    expect(handoffApi.confirmDesktopHandoff).not.toHaveBeenCalled()
    activity.loading = false
    await flushPromises()
    expect(batch).toHaveBeenCalledOnce()
    expect(batch).toHaveBeenCalledWith(targets)
    expect(single).not.toHaveBeenCalled()
  } finally { wrapper.unmount() }
})

it('只恢复采集页面时不自动执行', async () => {
  const { wrapper, single, batch } = await setup({ collect: { mode: 'browser', productUrl: '', platform: 'unknown' } })
  try {
    expect(handoffApi.confirmDesktopHandoff).toHaveBeenCalledOnce()
    expect(single).not.toHaveBeenCalled()
    expect(batch).not.toHaveBeenCalled()
  } finally { wrapper.unmount() }
})
