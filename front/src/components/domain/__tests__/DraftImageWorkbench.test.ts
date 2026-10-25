import { enableAutoUnmount, flushPromises, mount, type DOMWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, reactive, ref } from 'vue'
import DraftImageWorkbench from '../DraftImageWorkbench.vue'
import { createEmptyDraftDetail, createEmptyProduct } from '@/constants/initialState'
import type { DraftImageWorkbenchController } from '@/composables/useDraftImageWorkbench'
import type { ImageBatchSnapshot } from '@/api/imageBatches'
import type { ImageAsset } from '@/types/workflow'

enableAutoUnmount(afterEach)
function button(wrapper: { findAll(selector: string): DOMWrapper<Element>[] }, text: string) { return wrapper.findAll('button').find(item => item.text() === text)! }
function fixture() {
  const images = ['common', 'red', 'pink', 'old', 'result'].map(id => ({ id, url: `/${id}.png`, path: '', previewUrl: '', origin: 'source', usage: 'detail', platforms: [], selected: true, isMain: false, status: 'ready', width: 800, height: 800 } as ImageAsset))
  const product = reactive(createEmptyProduct())
  product.skuItems = ['red', 'pink', 'red'].map((id, index) => ({ id: `sku${index}`, name: `${id}款`, image_asset_id: id, active: true } as typeof product.skuItems[number]))
  const draft = reactive(createEmptyDraftDetail()); draft.draftId = 'd'; draft.language = 'ru'
  draft.images = [{ assetId: 'common', role: 'main', order: 0 }]
  draft.skuItems = product.skuItems.map(sku => ({ sku_id: sku.id, selected: true, overrides: {}, sku: sku.id, stock: '20', pricing: {}, publications: {}, attributes_by_target: {} }))
  const busy = ref(false), batch = ref<ImageBatchSnapshot | null>(null)
  const controller: DraftImageWorkbenchController = {
    busy: computed(() => busy.value), batch, notice: ref(''), undoLabel: computed(() => 'AI 修改 2 张'), redoLabel: computed(() => ''),
    start: vi.fn(), add: vi.fn(), upload: vi.fn(), remove: vi.fn(), reorder: vi.fn(), undo: vi.fn(), redo: vi.fn(),
  }
  const wrapper = mount(DraftImageWorkbench, { attachTo: document.body, props: { draft, product, images, loading: false, controller }, global: { stubs: { Teleport: true } } })
  return { wrapper, draft, product, images, controller, busy, batch }
}

describe('草稿图片 V2 工作台', () => {
  it('按当前引用去重，原位置替换后勾选发送当前结果，不显示历史版本卡片', async () => {
    const { wrapper, draft, controller } = fixture()
    expect(wrapper.findAll('[data-image-id]')).toHaveLength(3)
    expect(wrapper.find('[data-image-id="old"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('设置 SKU 主图')
    draft.skuItems[0].overrides.image_asset_id = 'result'
    draft.skuItems[2].overrides.image_asset_id = 'result'
    await flushPromises()
    expect(wrapper.find('[data-image-id="red"]').exists()).toBe(false)
    await wrapper.get('input[aria-label="选择处理图片 result"]').setValue(true)
    await button(wrapper, 'AI 修改').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('把刚修改的标题缩小')
    await button(wrapper, '开始处理 1 张').trigger('click')
    expect(controller.start).toHaveBeenCalledWith('edit', ['result'], '把刚修改的标题缩小')
    expect(wrapper.get('input[aria-label="选择处理图片 result"]').element).toHaveProperty('checked', false)
  })
  it('混选时只移出共用图，全选本区不影响另一区', async () => {
    const { wrapper, controller } = fixture()
    await wrapper.get('input[aria-label="选择处理图片 common"]').setValue(true)
    await button(wrapper.get('[data-region="sku"]'), '全选本区').trigger('click')
    expect(wrapper.text()).toContain('已选 3 张')
    await button(wrapper, '移出共用区（1）').trigger('click')
    await flushPromises()
    expect(wrapper.get('dialog[open]').text()).toContain('独立的 SKU 图片关联保留')
    await button(wrapper, '移出共用区').trigger('click')
    expect(controller.remove).toHaveBeenCalledWith(['common'])
    expect(wrapper.text()).toContain('勾选图片开始处理')
    await button(wrapper.get('[data-region="common"]'), '全选本区').trigger('click')
    await button(wrapper.get('[data-region="sku"]'), '全选本区').trigger('click')
    await button(wrapper.get('[data-region="sku"]'), '取消本区选择').trigger('click')
    expect(wrapper.text()).toContain('已选 1 张')
    expect(wrapper.get('input[aria-label="选择处理图片 common"]').element).toHaveProperty('checked', true)
  })
  it('放大、选择、排序互不干扰；弹窗正确区分拖出和遮罩点击', async () => {
    const { wrapper, draft, controller } = fixture()
    draft.images.push({ assetId: 'pink', role: 'detail', order: 1 })
    await flushPromises()
    await wrapper.get('[data-image-id="common"] button[aria-label^="排序"]').trigger('keydown', { key: 'ArrowRight' })
    expect(controller.reorder).toHaveBeenCalledWith(['pink', 'common'])
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
    await wrapper.get('[data-image-id="common"] button[aria-label^="放大"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('input[aria-label="选择处理图片 common"]').element).toHaveProperty('checked', false)
    const dialog = wrapper.get('dialog[open]')
    const pointer = { isPrimary: true, button: 0, pointerId: 1 }
    await dialog.get('img').trigger('pointerdown', pointer); await dialog.trigger('pointerup', pointer)
    expect(wrapper.find('dialog[open]').exists()).toBe(true)
    await dialog.trigger('pointerdown', pointer); await dialog.trigger('pointerup', pointer)
    await flushPromises()
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
    await wrapper.get('input[aria-label="选择处理图片 common"]').setValue(true)
    expect(wrapper.find('dialog[open]').exists()).toBe(false)
  })
  it('处理中允许勾选与查看，禁用提交、排序和撤销；失败项替换选择', async () => {
    const { wrapper, busy, batch } = fixture()
    busy.value = true
    await flushPromises()
    await wrapper.get('input[aria-label="选择处理图片 common"]').setValue(true)
    expect(button(wrapper, 'AI 修改').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button[title="撤销：AI 修改 2 张"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button[aria-label^="排序"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-image-id="red"] button[aria-label^="放大"]').attributes('disabled')).toBeUndefined()
    busy.value = false
    batch.value = { terminal: true, success: 1, failed: 1, items: [{ source_id: 'red', status: 'failed', error: '服务繁忙', result_urls: [] }] } as unknown as ImageBatchSnapshot
    await flushPromises()
    expect(wrapper.text()).toContain('服务繁忙')
    await button(wrapper, '选中失败项').trigger('click')
    expect(wrapper.get('input[aria-label="选择处理图片 common"]').element).toHaveProperty('checked', false)
    expect(wrapper.get('input[aria-label="选择处理图片 red"]').element).toHaveProperty('checked', true)
  })
  it('提交范围在确认窗口打开时固定，翻译与单张共用同一入口', async () => {
    const { wrapper, controller } = fixture()
    await wrapper.get('input[aria-label="选择处理图片 red"]').setValue(true)
    await button(wrapper, '翻译图片').trigger('click'); await flushPromises()
    await wrapper.get('input[aria-label="选择处理图片 red"]').setValue(false)
    await wrapper.get('input[aria-label="选择处理图片 pink"]').setValue(true)
    await button(wrapper, '开始处理 1 张').trigger('click')
    expect(controller.start).toHaveBeenCalledWith('translate', ['red'], '')
    expect(wrapper.text()).toContain('勾选图片开始处理')
  })
})
