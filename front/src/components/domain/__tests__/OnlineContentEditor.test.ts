import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { fetchOnlineSourceImages, type OnlineListing, type SourceImageSelection } from '@/api/onlineProducts'
import OnlineContentEditor from '../OnlineContentEditor.vue'

vi.mock('@/api/onlineProducts', () => ({fetchOnlineSourceImages: vi.fn()}))
const listing = {
  id: 'listing-1', platform: 'mercadolibre', content: {pictures: [
    {id: 'remote-1', url: 'https://images.example.com/1.jpg'},
    {id: 'remote-2', url: 'https://images.example.com/2.jpg'},
  ]}, capabilities: {content: {fields: ['pictures']}},
} as OnlineListing
const source: SourceImageSelection = {
  ok: true, local_product_id: 'product-1', local_draft_id: 'draft-1', reason: '', images: [
    {asset_id: 'asset-1', fingerprint: 'a'.repeat(64), preview_url: '/file?path=1', existing_picture_id: 'remote-1', existing_url: ''},
    {asset_id: 'asset-2', fingerprint: 'b'.repeat(64), preview_url: '/file?path=2', existing_picture_id: '', existing_url: ''},
    {asset_id: 'asset-3', fingerprint: 'c'.repeat(64), preview_url: '/file?path=3', existing_picture_id: '', existing_url: ''},
  ],
}
let wrapper: VueWrapper
beforeEach(() => {vi.clearAllMocks(); vi.mocked(fetchOnlineSourceImages).mockResolvedValue(source)})
afterEach(() => {wrapper?.unmount()})
function render(row = listing) {wrapper = mount(OnlineContentEditor, {props: {listing: row}})}
async function edit() {await wrapper.get('input[value="pictures"]').setValue(true)}
async function click(text: string) {await wrapper.findAll('button').find(button => button.text() === text)!.trigger('click'); await flushPromises()}
function pictures() {return wrapper.emitted('change')!.at(-1)![0] as {pictures: unknown[]}}

describe('在线商品图片编辑', () => {
  it('Yandex 属性展示名称，提交保持参数身份且不夹带展示名称', async () => {
    render({...listing,platform:'yandex',content:{attributes:[{id:'1',parameterId:1,name:'Материал',value:'Плюш',unitId:2}]},
      capabilities:{...listing.capabilities,content:{enabled:true,fields:['attributes'],scope:'商品',reason:''}}})
    expect(wrapper.text()).toContain('Материал')
    expect(wrapper.text()).toContain('属性编号：1')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('input:not([type="checkbox"])').setValue('Хлопок')
    expect(wrapper.emitted('change')!.at(-1)![0]).toEqual({attributes:[{id:'1',parameterId:1,value:'Хлопок',unitId:2}]})
  })
  it('按钮提供 hint，边界禁止移动，删除不清空图集', async () => {
    render()
    expect(wrapper.get('[aria-label="右移图片 1"]').attributes('title')).toBe('请先勾选商品图片')
    await edit()
    expect(wrapper.get('[aria-label="左移图片 1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[aria-label="右移图片 1"]').attributes('title')).toBe('向右移动')
    expect(wrapper.get('[aria-label="删除图片 1"]').attributes('title')).toContain('源草稿图片仍保留')
    await wrapper.get('[aria-label="右移图片 1"]').trigger('click')
    expect(pictures().pictures).toEqual([listing.content.pictures![1], listing.content.pictures![0]])
    await wrapper.get('[aria-label="删除图片 1"]').trigger('click')
    expect(pictures().pictures).toEqual([listing.content.pictures![0]])
    expect(wrapper.get('[aria-label="删除图片 1"]').attributes('title')).toBe('至少保留一张图片')
    expect(wrapper.get('[aria-label="删除图片 1"]').attributes('disabled')).toBeDefined()
    expect(listing.content.pictures).toHaveLength(2)
  })

  it('仅从当前商品源草稿多选，排除重复，提交资产引用并独立提供预览', async () => {
    render(); await edit(); await click('从源草稿添加图片')
    expect(fetchOnlineSourceImages).toHaveBeenCalledWith('listing-1')
    expect(wrapper.get('[aria-label="选择源草稿图片 1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[aria-label="选择源草稿图片 3"]').trigger('click')
    await wrapper.get('[aria-label="选择源草稿图片 2"]').trigger('click')
    await click('添加选中图片（2）')
    expect(pictures().pictures.slice(2)).toEqual([
      {asset_id: 'asset-3', fingerprint: 'c'.repeat(64)}, {asset_id: 'asset-2', fingerprint: 'b'.repeat(64)},
    ])
    expect(wrapper.emitted('picturePreviews')!.at(-1)![0]).toEqual([
      'https://images.example.com/1.jpg', 'https://images.example.com/2.jpg', '/file?path=3', '/file?path=2',
    ])
    await click('从源草稿添加图片')
    expect(wrapper.get('[aria-label="选择源草稿图片 2"]').text()).toBe('已添加')
    expect(wrapper.find('input[type="url"]').exists()).toBe(false)
  })

  it('未关联和读取失败有明确提示，失败可重试', async () => {
    vi.mocked(fetchOnlineSourceImages).mockRejectedValueOnce(new Error('读取失败'))
    render(); await edit(); await click('从源草稿添加图片')
    expect(wrapper.get('[role="alert"]').text()).toBe('读取失败')
    vi.mocked(fetchOnlineSourceImages).mockResolvedValue({...source, images: [], reason: '未关联源草稿，暂不能添加图片'})
    await click('重新读取')
    expect(wrapper.get('[role="status"]').text()).toContain('未关联源草稿')
    expect(wrapper.findAll('[data-testid="target-picture"]')).toHaveLength(2)
  })

  it('限制完整图集不超过 30 张', async () => {
    render({...listing, content: {pictures: Array.from({length: 29}, (_, i) => ({id: `id-${i}`, url: `https://images.example.com/${i}`}))}})
    await edit(); await click('从源草稿添加图片')
    await wrapper.get('[aria-label="选择源草稿图片 2"]').trigger('click')
    await wrapper.get('[aria-label="选择源草稿图片 3"]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('超过 30 张')
    expect(wrapper.findAll('button').find(button => button.text() === '添加选中图片（2）')!.attributes('disabled')).toBeDefined()
  })

  it('图片失败显示占位，重试仍保留图片操作', async () => {
    render(); await edit()
    await wrapper.find('img').trigger('error')
    expect(wrapper.text()).toContain('图片加载失败')
    await wrapper.get('[aria-label="重新加载商品图片 1"]').trigger('click')
    expect(wrapper.findAll('img')).toHaveLength(2)
    expect(pictures().pictures).toHaveLength(2)
  })
})
