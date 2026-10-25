import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { fetchOnlineAttributes, fetchOnlineAttributeValues, type OnlineListing, type OnlineAttributeDefinition } from '@/api/onlineProducts'
import OnlineYandexAttributes from '../OnlineYandexAttributes.vue'

vi.mock('@/api/onlineProducts', () => ({fetchOnlineAttributes: vi.fn(), fetchOnlineAttributeValues: vi.fn()}))
const definition = (id: string, name: string, extra: Partial<OnlineAttributeDefinition> = {}): OnlineAttributeDefinition => ({
  id, name, required: true, read_only: false, managed_by: '', value_type: 'enum', value_mode: 'strict_enum',
  allow_custom_values: false, is_dictionary: true, is_collection: false, max_value_count: null,
  default_unit_id: '', unit_options: [], options: [], has_more_values: false, format_hint: '', ...extra,
})
const shoe = definition('10', 'Размер обуви', {options: [{dictionary_value_id:'101',value:'42-43'}]})
const grid = definition('11', 'Размерная сетка', {options:[{dictionary_value_id:'111',value:'EU'}]})
const listing = {id:'listing',version:'v1',platform:'yandex',content:{attributes:[]}} as unknown as OnlineListing
let wrapper: VueWrapper
const page = (attributes: OnlineAttributeDefinition[], next = '') => ({ok:true,listing_id:'listing',version:'v1',current_values:[],
  page:{category_id:'100',attributes,has_more:!!next,next_cursor:next}})
function changes() {return wrapper.emitted('change')!.at(-1)![0]}
function validity() {return wrapper.emitted('validity')!.at(-1)![0]}
async function render(row = listing) {wrapper = mount(OnlineYandexAttributes,{props:{listing:row}}); await flushPromises()}
async function click(text: string) {await wrapper.findAll('button').find(button=>button.text()===text)!.trigger('click'); await flushPromises()}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchOnlineAttributes).mockResolvedValue(page([shoe,grid]))
  vi.mocked(fetchOnlineAttributeValues).mockResolvedValue({ok:true,listing_id:'listing',version:'v1',current_values:[],
    page:{attribute_id:'10',values:[{dictionary_value_id:'101',value:'42-43'}],has_more:false,next_cursor:''}})
})
afterEach(() => wrapper?.unmount())

describe('Yandex 在线类目属性', () => {
  it('加载所有字段页，提示缺失必填项，勾选完整后提交真实枚举身份', async () => {
    vi.mocked(fetchOnlineAttributes).mockResolvedValueOnce(page([shoe],'offset:1')).mockResolvedValueOnce(page([grid]))
    await render()
    expect(fetchOnlineAttributes).toHaveBeenNthCalledWith(2,'listing','v1','offset:1')
    expect(wrapper.text()).toContain('必填 · 未填写')
    expect(changes()).toEqual([])
    await wrapper.get('[aria-label="修改 Размер обуви"]').setValue(true)
    expect(validity()).toBe(false)
    await wrapper.get('[aria-label="Размер обуви 值 1"]').setValue('101')
    await wrapper.get('[aria-label="修改 Размерная сетка"]').setValue(true)
    await wrapper.get('[aria-label="Размерная сетка 值 1"]').setValue('111')
    expect(validity()).toBe(true)
    expect(changes()).toEqual([{id:'10',parameterId:10,value:'42-43',valueId:'101'},{id:'11',parameterId:11,value:'EU',valueId:'111'}])
    expect(wrapper.emitted('names')!.at(-1)![0]).toEqual({'10':'Размер обуви','11':'Размерная сетка'})
    expect(listing.content.attributes).toEqual([])
  })

  it('已有枚举和多值可改，未勾选字段不会进入变更', async () => {
    const multi = definition('12','颜色',{is_collection:true,max_value_count:2,options:[{dictionary_value_id:'121',value:'红'},{dictionary_value_id:'122',value:'蓝'}]})
    vi.mocked(fetchOnlineAttributes).mockResolvedValue({...page([shoe,multi]),current_values:[{id:'12',parameterId:12,valueId:'121',value:'红'},{id:'12',parameterId:12,valueId:'122',value:'蓝'}]})
    await render({...listing,content:{attributes:[{id:'12',parameterId:12,valueId:'121',value:'红'},{id:'12',parameterId:12,valueId:'122',value:'蓝'}]}})
    await wrapper.get('[aria-label="修改 颜色"]').setValue(true)
    await wrapper.get('[aria-label="移除 颜色 值 1"]').trigger('click')
    expect(changes()).toEqual([{id:'12',parameterId:12,valueId:'122',value:'蓝'}])
    await click('添加一个值')
    expect(validity()).toBe(false)
    await wrapper.get('[aria-label="颜色 值 2"]').setValue('121')
    expect(changes()).toEqual([{id:'12',parameterId:12,valueId:'122',value:'蓝'},{id:'12',parameterId:12,valueId:'121',value:'红'}])
    expect(wrapper.findAll('button').some(button=>button.text()==='添加一个值')).toBe(false)
    await wrapper.get('[aria-label="修改 颜色"]').setValue(false)
    expect(changes()).toEqual([])
  })

  it('候选搜索分页，查询变化不复用旧游标，读取失败可重试', async () => {
    vi.mocked(fetchOnlineAttributes).mockResolvedValue(page([shoe]))
    await render()
    await wrapper.get('[aria-label="修改 Размер обуви"]').setValue(true)
    vi.mocked(fetchOnlineAttributeValues).mockResolvedValueOnce({ok:true,listing_id:'listing',version:'v1',current_values:[],page:{attribute_id:'10',values:[],has_more:true,next_cursor:'offset:50'}})
    await wrapper.get('input[type="search"][placeholder="搜索平台候选值"]').setValue('42')
    await click('搜索')
    await click('加载更多候选值')
    expect(fetchOnlineAttributeValues).toHaveBeenLastCalledWith('listing','v1','10','42','offset:50')
    vi.mocked(fetchOnlineAttributeValues).mockRejectedValueOnce(new Error('候选查询失败'))
    await click('搜索')
    expect(wrapper.text()).toContain('候选查询失败')
    await click('重试')
    expect(wrapper.text()).not.toContain('候选查询失败')
  })

  it('只读字段不可改，开放枚举支持自定义并保留单位', async () => {
    vi.mocked(fetchOnlineAttributes).mockResolvedValue(page([
      definition('15','材质',{allow_custom_values:true,default_unit_id:'8',unit_options:[{id:'8',name:'см'}]}),
      definition('16','只读',{read_only:true}),
    ]))
    await render()
    expect(wrapper.get('[aria-label="修改 只读"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[aria-label="修改 材质"]').setValue(true)
    await wrapper.get('[aria-label="材质 自定义值 1"]').setValue('其他')
    expect(changes()).toEqual([{id:'15',parameterId:15,value:'其他',unitId:8}])
    expect(validity()).toBe(true)
  })

  it('规则失败不提供任意输入入口，重试恢复；过期版本拒绝展示', async () => {
    vi.mocked(fetchOnlineAttributes).mockRejectedValueOnce(new Error('类目读取失败'))
    await render()
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.text()).toContain('类目读取失败')
    await click('重新读取')
    expect(wrapper.text()).toContain('Размер обуви')
    vi.mocked(fetchOnlineAttributes).mockResolvedValue({...page([shoe]),version:'stale'})
    await wrapper.setProps({listing:{...listing,version:'v2'}})
    await flushPromises()
    expect(wrapper.text()).toContain('重新打开编辑器')
    expect(wrapper.find('select').exists()).toBe(false)
  })
  it('超出安全整数范围的枚举编号从当前值到新选择都保持字符串', async () => {
    const big = '18446744073709551614'
    vi.mocked(fetchOnlineAttributes).mockResolvedValue({...page([{...shoe,options:[{dictionary_value_id:big,value:'42-43'}]}]),
      current_values:[{id:'10',parameterId:10,value:'42-43',valueId:big}]})
    await render({...listing,content:{attributes:[{id:'10',parameterId:10,value:'42-43',valueId:Number(big)}]}})
    await wrapper.get('[aria-label="修改 Размер обуви"]').setValue(true)
    expect(changes()).toEqual([{id:'10',parameterId:10,value:'42-43',valueId:big}])
    await wrapper.get('[aria-label="Размер обуви 值 1"]').setValue(big)
    expect(changes()).toEqual([{id:'10',parameterId:10,value:'42-43',valueId:big}])
  })

})
