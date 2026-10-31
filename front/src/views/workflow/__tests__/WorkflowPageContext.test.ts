import { shallowMount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createI18n } from 'vue-i18n'
import zh from '@/i18n/locales/zh'
import { afterEach, expect, it, vi } from 'vitest'
import WorkflowView from '../WorkflowView.vue'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import type { DraftIndexItem } from '@/types/workflow'

afterEach(() => vi.restoreAllMocks())

it('真实工作台先传勾选范围，打开和切换编辑草稿后优先传编辑对象，关闭及离开时清理', async () => {
  localStorage.removeItem('ai-chat-read-background')
  const pinia = createPinia()
  setActivePinia(pinia)
  const workflow = useWorkflowStore()
  const catalog = useWorkflowCatalogStore()
  const context = useAiPageContextStore()
  vi.spyOn(workflow, 'loadState').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'hydrateTab').mockResolvedValue(undefined)
  vi.spyOn(workflow, 'loadDraft').mockImplementation(async (item) => {
    catalog.currentDraft = { ...createEmptyDraftDetail(), draftId: item.draftId, productId: item.productId }
  })
  const rows: DraftIndexItem[] = ['a', 'b'].map(id => ({
    draftId: `draft-${id}`, productId: `product-${id}`, title: id,
    platform: 'yandex', status: 'claimed', targetSites: [], language: 'ru-RU',
    sourceProductId: '', platforms: ['yandex'], site: 'global', productTitle: id,
    mainImage: '', sourcePlatform: '1688', sourceUrl: '', categoryId: '', categoryPath: '',
    publishStatus: '', createdAt: '', updatedAt: '', productFilePath: '', raw: {},
  }))
  catalog.draftsIndex = rows
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'WorkflowHome', component: WorkflowView },
  ] })
  await router.push('/?tab=drafts')
  const wrapper = shallowMount(WorkflowView, {
    global: { plugins: [pinia, router, createI18n({ legacy: false, locale: 'zh', messages: { zh } })], stubs: { DraftBoxPanel: false } },
  })
  try {
    await flushPromises()
    await wrapper.get('[aria-label="全选当前草稿"]').setValue(true)
    expect(context.snapshot()).toEqual({ page: 'drafts', selected_draft_ids: ['draft-a', 'draft-b'] })
    const box = wrapper.getComponent(DraftBoxPanel)
    box.vm.$emit('edit', rows[0])
    await flushPromises()
    expect(context.snapshot()).toEqual({ page: 'draft_editor', section: 'text', draft_id: 'draft-a', product_id: 'product-a' })
    box.vm.$emit('edit', rows[1])
    await flushPromises()
    expect(context.snapshot()?.draft_id).toBe('draft-b')
    wrapper.getComponent({ name: 'DraftWorkspacePanel' }).vm.$emit('close')
    await flushPromises()
    expect(context.snapshot()).toEqual({ page: 'drafts', selected_draft_ids: ['draft-a', 'draft-b'] })
    await router.push('/?tab=library')
    await flushPromises()
    expect(context.snapshot()).toEqual({ page: 'library' })
  } finally {
    wrapper.unmount()
  }
  expect(context.snapshot()).toBeUndefined()
})
