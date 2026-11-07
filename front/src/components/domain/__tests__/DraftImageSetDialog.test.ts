import { enableAutoUnmount, flushPromises, mount, type DOMWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import DraftImageSetDialog from '../DraftImageSetDialog.vue'
import { createEmptyDraftDetail } from '@/constants/initialState'
import type { DraftImageWorkbenchController } from '@/composables/useDraftImageWorkbench'
import type { ImageBatchSnapshot } from '@/api/imageBatches'
import type { ImageSetPlan } from '@/api/draftImages'
import type { ImageAsset } from '@/types/workflow'

enableAutoUnmount(afterEach)
function button(wrapper: { findAll(selector: string): DOMWrapper<Element>[] }, text: string) {
  return wrapper.findAll('button').find(item => item.text() === text)!
}
const plan: ImageSetPlan = { style: '统一白色背景', pages: [
  { key: 'main', title: '主图', role: 'main', prompt: '重新构图展示商品', source_image_ids: ['source'] },
  { key: 'detail', title: '细节', role: 'detail', prompt: '展示已有材质细节', source_image_ids: ['source'] },
] }
function snapshot(): ImageBatchSnapshot {
  return { conversation_id: 'set', product_id: 'p', draft_id: 'd', prompt: '生成套图', created_at: '', updated_at: '',
    total: 2, completed: 2, success: 2, failed: 0, queued: 0, running: 0, terminal: true, image_set: plan,
    items: plan.pages.map((page, index) => ({ index, source_id: page.key, source_url: '', status: 'success', error: '',
      outcome_unknown: false, started_at: '', finished_at: '', result_asset_ids: [page.key], result_urls: [`/${page.key}.png`] })),
  }
}
async function fixture(saved?: ImageBatchSnapshot, withSkus = false) {
  const draft = createEmptyDraftDetail(); draft.draftId = 'd'; draft.updatedAt = 'version'; draft.language = 'ru'
  const images: ImageAsset[] = ['source', 'main', 'detail', 'red-original', 'red-result'].map(id => ({ id, url: `/${id}.png`, path: '', previewUrl: '', origin: 'source', usage: 'detail', platforms: [], selected: true, isMain: false, status: 'ready', width: 800, height: 800 }))
  const batch = ref<ImageBatchSnapshot | null>(saved || null), busy = ref(false)
  const controller: DraftImageWorkbenchController = {
    busy: computed(() => busy.value), batch, notice: ref(''), undoLabel: computed(() => ''), redoLabel: computed(() => ''),
    start: vi.fn(), add: vi.fn(), upload: vi.fn(), remove: vi.fn(), reorder: vi.fn(), undo: vi.fn(), redo: vi.fn(),
    planSet: vi.fn(async () => ({ ok: true, plan: structuredClone(plan), updated_at: 'planned-version' })), generateSet: vi.fn(), applySet: vi.fn(),
  }
  const skus = withSkus ? ['red-small', 'red-large'].map(skuId => ({ skuId, name: skuId, imageAssetId: 'red-original', inherited: true, selected: true })) : []
  const wrapper = mount(DraftImageSetDialog, { attachTo: document.body, props: { open: false, draft, images, referenceIds: ['source'], controller, skus },
    global: { stubs: { Teleport: true } } })
  await wrapper.setProps({ open: true }); await flushPromises()
  return { wrapper, controller, batch, busy }
}

describe('共用套图规划与采用', () => {
  it('共用同一原图的 SKU 合并展示，并把明确的规格范围交给规划', async () => {
    const { wrapper, controller } = await fixture(undefined, true)
    expect(wrapper.findAll('input[aria-label^="生成 SKU 图"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('red-small、red-large')
    expect(wrapper.text()).toContain('预计生成 7 张图片')
    await button(wrapper, '自动规划套图').trigger('submit'); await flushPromises()
    expect(controller.planSet).toHaveBeenCalledWith(['source'], 6, '', ['red-small', 'red-large'])
  })
  it('只生成共用主图时只提交共用参考素材', async () => {
    const { wrapper, controller } = await fixture(undefined, true)
    await wrapper.get('input[value="common"]').setValue()
    expect(wrapper.find('input[aria-label^="生成 SKU 图"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('预计生成 6 张图片')
    await wrapper.get('form').trigger('submit')
    expect(controller.planSet).toHaveBeenCalledWith(['source'], 6, '', [])
  })
  it('只生成 SKU 图时只提交已选规格，缺少目标时不能提交', async () => {
    const { wrapper, controller } = await fixture(undefined, true)
    await wrapper.get('input[value="sku"]').setValue()
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.find('input[aria-label^="套图参考"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('预计生成 1 张图片')
    const sku = wrapper.get('input[aria-label^="生成 SKU 图"]')
    await sku.setValue(false)
    await wrapper.get('form').trigger('submit')
    expect(controller.planSet).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('预计生成 0 张图片')
    await wrapper.get('input[aria-label^="生成 SKU 图"]').setValue(true)
    expect(wrapper.text()).toContain('预计生成 1 张图片')
    expect(button(wrapper, '自动规划套图').attributes('disabled')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    expect(controller.planSet).toHaveBeenCalledWith([], undefined, '', ['red-small', 'red-large'])
  })
  it('SKU 结果标明绑定规格，可单独采用且不会参与共用图排序', async () => {
    const saved = snapshot()
    saved.image_set = { style: plan.style, pages: [...plan.pages, {
      key: 'sku-red', title: '红色规格图', role: 'main', prompt: '规格主图', source_image_ids: ['red-original'], scope: 'sku', sku_ids: ['red-small', 'red-large'],
    }] }
    saved.items.push({ ...saved.items[0], index: 2, source_id: 'sku-red', result_asset_ids: ['red-result'] })
    const { wrapper, controller } = await fixture(saved, true)
    const skuCard = wrapper.get('[data-set-page="sku-red"]')
    expect(skuCard.text()).toContain('SKU 图 · red-small、red-large')
    expect(skuCard.find('button[aria-label^="前移"]').exists()).toBe(false)
    await wrapper.get('input[aria-label="采用 主图"]').setValue(false)
    await wrapper.get('input[aria-label="采用 细节"]').setValue(false)
    await button(wrapper, '采用 1 张并更新图片').trigger('click')
    expect(controller.applySet).toHaveBeenCalledWith([], ['sku-red'])
  })
  it('先规划整套图片，允许调整风格与设计后再生成', async () => {
    const { wrapper, controller } = await fixture()
    await wrapper.get('select').setValue('2')
    await wrapper.get('textarea').setValue('简洁风格')
    await button(wrapper, '自动规划套图').trigger('submit'); await flushPromises()
    expect(controller.planSet).toHaveBeenCalledWith(['source'], 2, '简洁风格', [])
    expect(controller.generateSet).not.toHaveBeenCalled()
    await wrapper.findAll('textarea')[0].setValue('统一浅灰背景')
    await wrapper.findAll('textarea')[1].setValue('保留商品颜色，重新设计主图')
    await button(wrapper, '开始生成 2 张').trigger('submit')
    expect(controller.generateSet).toHaveBeenCalledWith(expect.objectContaining({ style: '统一浅灰背景', pages: expect.arrayContaining([
      expect.objectContaining({ prompt: '保留商品颜色，重新设计主图' }),
    ]) }), 'planned-version')
    expect(controller.applySet).not.toHaveBeenCalled()
  })
  it('恢复已有结果，调整采用顺序，单张重做使用原任务', async () => {
    const { wrapper, controller } = await fixture(snapshot())
    expect(wrapper.findAll('[data-set-page]')).toHaveLength(2)
    await wrapper.get('button[aria-label="前移 细节"]').trigger('click')
    expect(wrapper.text()).toContain('1. 细节 → 2. 主图')
    await button(wrapper, '采用 2 张并替换共用图').trigger('click')
    expect(controller.applySet).toHaveBeenCalledWith([
      { assetId: 'detail', role: 'main', order: 0, label: '细节' },
      { assetId: 'main', role: 'detail', order: 1, label: '主图' },
    ])
    await wrapper.get('button[aria-label="重做 主图"]').trigger('click')
    expect(controller.generateSet).toHaveBeenCalledWith(undefined, 'version', ['main'])
  })
  it('部分失败只采用成功项；取消采用不会因重新读取结果而恢复', async () => {
    const saved = snapshot(); saved.items[1].status = 'failed'; saved.items[1].error = '服务繁忙'; saved.success = 1; saved.failed = 1
    const { wrapper, controller, batch } = await fixture(saved)
    expect(wrapper.text()).toContain('服务繁忙')
    await wrapper.get('input[aria-label="采用 主图"]').setValue(false)
    batch.value = structuredClone(saved); await flushPromises()
    expect(button(wrapper, '采用 0 张并替换共用图').attributes('disabled')).toBeDefined()
    expect(controller.applySet).not.toHaveBeenCalled()
    expect(wrapper.find('input[aria-label="采用 细节"]').exists()).toBe(false)
  })
  it('处理中禁用重做和采用，允许关闭后重新查看', async () => {
    const { wrapper, busy } = await fixture(snapshot())
    busy.value = true; await flushPromises()
    expect(button(wrapper, '采用 2 张并替换共用图').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button[aria-label="重做 主图"]').attributes('disabled')).toBeDefined()
    await button(wrapper, '关闭').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('完成先后不改变默认采用顺序', async () => {
    const partial = snapshot(); partial.terminal = false; partial.completed = 1; partial.success = 1; partial.running = 1
    partial.items[0].status = 'running'; partial.items[0].result_asset_ids = []
    const { wrapper, batch } = await fixture(partial)
    expect(wrapper.text()).toContain('1. 细节')
    batch.value = snapshot(); await flushPromises()
    expect(wrapper.text()).toContain('1. 主图 → 2. 细节')
  })
  it('沿用弹窗手势：从内容拖出不关闭，正常遮罩点击关闭', async () => {
    const { wrapper } = await fixture()
    const dialog = wrapper.get('dialog[open]'), pointer = { isPrimary: true, button: 0, pointerId: 1 }
    await dialog.get('textarea').trigger('pointerdown', pointer); await dialog.trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await dialog.trigger('pointerdown', pointer); await dialog.trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
