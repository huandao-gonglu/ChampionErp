import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import OzonExportDialog from '../OzonExportDialog.vue'
import { generateOzonExport, previewOzonExport, type OzonExportPreview } from '@/api/ozonExport'

vi.mock('@/api/ozonExport', () => ({ generateOzonExport: vi.fn(), previewOzonExport: vi.fn() }))
const preview: OzonExportPreview = {
  ok: true, template: { category: '圣诞装饰品', category_id: '43429543', currency: 'CNY', required_fields: ['货号', '价格'] },
  rows: [{ listing_id: 'one', seller_sku: 'SKU-01', version: 'v1', title_before: '原名称20×20', title_changed: true,
    fields: { title: '修正名称25×25', price: '37', weight_g: '180', width_mm: '260', height_mm: '20', length_mm: '260', brand: '诚实', model: '圣诞组合', barcode: '', main_image: 'https://images.example.com/one.jpg', variant: 'EE031', size_cm: '25' }, errors: [], warnings: ['未提供条码，将留空'] }],
  summary: { total: 1, ready: 1, missing: 0, barcode_missing: 1, title_changed: 1 }, preview_fingerprint: 'preview-v1',
}
let wrapper: VueWrapper
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(previewOzonExport).mockResolvedValue(structuredClone(preview))
  vi.mocked(generateOzonExport).mockResolvedValue({ ok: true, filename: '导出.xlsx', file_base64: btoa('xlsx bytes') })
  vi.stubGlobal('FileReader', class {
    result = 'data:application/octet-stream;base64,dGVtcGxhdGU='
    onload: (()=>void) | null = null
    readAsDataURL() { this.onload?.() }
  })
  wrapper = mount(OzonExportDialog, { props: { open: true, accountId: 'shop', listingIds: ['one'] }, attachTo: document.body, global: { stubs: { Teleport: true } } })
})
afterEach(() => { wrapper.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function upload() {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['模板'], '圣诞装饰品.xlsx')] })
  await input.trigger('change'); await flushPromises()
}
async function button(text: string) {
  const target = wrapper.findAll('button').find(b=>b.text()===text)
  expect(target, text).toBeDefined(); await target!.trigger('click'); await flushPromises()
}
describe('Ozon 模板导出弹窗', () => {
  it('上传、检查、生成与下载贯通，并明确生成不代表已上架', async () => {
    await upload()
    expect(previewOzonExport).toHaveBeenCalledWith(expect.objectContaining({ account_id: 'shop', listing_ids: ['one'], template_base64: 'dGVtcGxhdGU=' }))
    expect(wrapper.text()).toContain('圣诞装饰品')
    await button('下一步：检查商品')
    expect(wrapper.get('[data-testid="ozon-export-row"]').text()).toContain('260×260×20')
    expect(wrapper.text()).not.toContain('试传')
    await button('查看修正')
    expect(wrapper.text()).toContain('原名称20×20')
    const titleDialog = wrapper.findAll('dialog').find(d=>d.text().includes('查看标题修正'))!
    await titleDialog.get('header button').trigger('click'); await flushPromises()
    await button('生成 1 个 SKU 文件')
    expect(generateOzonExport).toHaveBeenCalledWith(expect.objectContaining({ listing_ids: ['one'], preview_fingerprint: 'preview-v1' }))
    expect(wrapper.text()).toContain('文件生成完成不代表商品已在 Ozon 上架')
    const create = vi.fn().mockReturnValue('blob:download')
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await button('下载 XLSX 文件')
    expect(create.mock.calls[0][0]).toBeInstanceOf(Blob)
    expect(anchorClick).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('已开始下载')
  })
  it('编辑只传当前导出覆盖值，重新检查后的版本用于生成', async () => {
    await upload(); await button('下一步：检查商品'); await button('编辑')
    expect(wrapper.text()).toContain('仅修改本次导出')
    await wrapper.get('input[aria-label="价格 CNY"]').setValue('39.50')
    vi.mocked(previewOzonExport).mockResolvedValue({ ...preview, preview_fingerprint: 'edited-v2' })
    await button('保存并检查')
    expect(previewOzonExport).toHaveBeenLastCalledWith(expect.objectContaining({ overrides: { one: expect.objectContaining({ price: '39.50' }) } }))
    await button('生成 1 个 SKU 文件')
    expect(generateOzonExport).toHaveBeenCalledWith(expect.objectContaining({ preview_fingerprint: 'edited-v2' }))
  })
  it('缺少必填资料时阻止生成，错误模板可重新选择', async () => {
    vi.mocked(previewOzonExport).mockRejectedValueOnce(new Error('当前支持圣诞装饰品类目'))
    await upload(); expect(wrapper.get('[role="alert"]').text()).toContain('圣诞装饰品')
    expect(wrapper.findAll('button').find(b=>b.text()==='下一步：检查商品')!.attributes('disabled')).toBeDefined()
    vi.mocked(previewOzonExport).mockResolvedValue({ ...preview, rows: [{ ...preview.rows[0], errors: ['缺少价格'] }], summary: { ...preview.summary, ready: 0, missing: 1 } })
    await upload(); await button('下一步：检查商品')
    expect(wrapper.get('[data-testid="ozon-export-row"]').text()).toContain('缺少价格')
    expect(wrapper.findAll('button').find(b=>b.text()==='生成 1 个 SKU 文件')!.attributes('disabled')).toBeDefined()
    expect(generateOzonExport).not.toHaveBeenCalled()
  })
  it('店铺切换后丢弃迟到的旧模板检查结果', async () => {
    let finish!: (result: OzonExportPreview)=>void
    vi.mocked(previewOzonExport).mockReturnValue(new Promise(resolve=>{ finish=resolve }))
    await upload()
    await wrapper.setProps({ accountId: 'other-shop' })
    finish(preview); await flushPromises()
    expect(wrapper.findAll('button').find(b=>b.text()==='下一步：检查商品')!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('圣诞装饰品.xlsx')
  })
  it('内部按下外部松开不关闭，完整遮罩点击可关闭', async () => {
    await flushPromises()
    const dialog = wrapper.findAll('dialog')[0], pointer = { isPrimary: true, button: 0, pointerId: 1 }
    await wrapper.get('input[type="file"]').trigger('pointerdown', pointer)
    await dialog.trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toBeUndefined()
    await dialog.trigger('pointerdown', pointer); await dialog.trigger('pointerup', pointer)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
