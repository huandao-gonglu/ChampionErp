import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import OnlineProductsPanel from '../OnlineProductsPanel.vue'
import { fetchOnlineDetail, fetchOnlineProducts, onlineAction, type OnlineListing, type OnlinePage } from '@/api/onlineProducts'

vi.mock('@/api/onlineProducts', () => ({ fetchOnlineDetail: vi.fn(), fetchOnlineProducts: vi.fn(), onlineAction: vi.fn() }))
const item: OnlineListing = {
  id: 'remote-1', platform: 'mercadolibre', account_id: 'shop-1', remote_id: 'CBT123', model: 'traditional_global_items',
  buyer_links: [], title: '店铺同步商品', seller_sku: 'seller-sku', thumbnail: '', raw_status: 'active', sale_state: 'active', raw_sub_status: [], version: 'v1', synced_at: '', errors: [], desired_sale_state: '',
  prices: [{id:'global',label:'全局基础价',amount:'12',currency:'USD',kind:'base_price',writable:true,reason:''}], stocks: [], markets: [], content: {title:'店铺同步商品'},
  capabilities: {price:{enabled:true,fields:[],scope:'全局',reason:''},stock:{enabled:false,fields:[],scope:'',reason:'未取得库存位置'},content:{enabled:true,fields:['title'],scope:'全局',reason:''},sale_state:{enabled:true,fields:[],scope:'全局',reason:''}},
}
function response(): OnlinePage { return {items:[item],total:1,page:1,per_page:25,account_id:'shop-1',store_name:'当前店铺',state:'ready',markets:[],statuses:['active'],summary:{total:1,active:1,paused:0,attention:0},latest_sync:null,jobs:[]} }
let wrapper: VueWrapper | undefined
beforeEach(() => {setActivePinia(createPinia());vi.useFakeTimers(); vi.clearAllMocks();vi.mocked(fetchOnlineProducts).mockResolvedValue(response());vi.mocked(fetchOnlineDetail).mockResolvedValue(item)})
afterEach(() => {wrapper?.unmount();wrapper=undefined;document.body.innerHTML='';vi.useRealTimers()})
function render(){wrapper=mount(OnlineProductsPanel,{attachTo:document.body,global:{stubs:{Teleport:true}}});return wrapper}
async function click(text:string){const button=wrapper!.findAll('button').find(b=>b.text()===text);expect(button, text).toBeDefined();await button!.trigger('click');await flushPromises()}

describe('在线商品页面',()=>{
  it('AI 背景使用当前平台与在线刊登 ID，关闭详情和卸载后清理',async()=>{
    render();await flushPromises()
    const context=useAiPageContextStore()
    expect(context.current).toMatchObject({page:'onlineProducts',platform:'mercadolibre'})
    await click('管理 →')
    expect(context.current?.listing_id).toBe('remote-1')
    expect(context.current?.draft_id).toBeUndefined()
    await click('关闭 ×')
    expect(context.current?.listing_id).toBeUndefined()
    await click('Yandex Market')
    expect(context.current?.platform).toBe('yandex')
    wrapper!.unmount();wrapper=undefined
    expect(context.current).toBeNull()
  })
  it('展示 API 商品并读取详情，页面加载不会发起平台修改',async()=>{
    render();await flushPromises()
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(1)
    expect(wrapper!.text()).toContain('店铺同步商品')
    await click('管理 →')
    expect(fetchOnlineDetail).toHaveBeenCalledWith('remote-1')
    expect(wrapper!.findAll('button').find(b=>b.text()==='修改库存')!.attributes('disabled')).toBeDefined()
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('单一买家链接在列表和详情直接打开新窗口，不提交商品修改',async()=>{
    const url='https://market.yandex.ru/card/slug/123?businessId=456'
    const product={...item,platform:'yandex' as const,buyer_links:[{label:'Yandex Market',url,site_id:'B2C'}]}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[product]})
    vi.mocked(fetchOnlineDetail).mockResolvedValue(product)
    render();await flushPromises()
    const link=wrapper!.get('[data-testid="online-listing"] a')
    expect(link.attributes('href')).toBe(url)
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
    await click('管理 →')
    expect(wrapper!.get('[aria-label="商品详情"] a').attributes('href')).toBe(url)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('多个销售站点保留各自的买家链接，让用户选择',async()=>{
    const links=[
      {label:'墨西哥 · MLM1',site_id:'MLM',url:'https://articulo.mercadolibre.com.mx/MLM-1-producto-_JM'},
      {label:'巴西 · MLB2',site_id:'MLB',url:'https://produto.mercadolivre.com.br/MLB-2-produto-_JM'},
    ]
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[{...item,buyer_links:links}]})
    render();await flushPromises()
    const choices=wrapper!.get('[data-testid="online-listing"] details')
    expect(choices.get('summary').text()).toBe('查看买家页面')
    expect(choices.findAll('a').map(link=>link.attributes('href'))).toEqual(links.map(link=>link.url))
    expect(choices.text()).toContain('墨西哥 · MLM1')
    expect(choices.text()).toContain('巴西 · MLB2')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('平台没有返回链接时不生成猜测地址',async()=>{
    render();await flushPromises()
    const row=wrapper!.get('[data-testid="online-listing"]')
    expect(row.text()).toContain('暂无买家链接')
    expect(row.find('a').exists()).toBe(false)
  })
  it('价格需预览和确认才提交，保留所选范围与币种',async()=>{
    vi.mocked(onlineAction).mockResolvedValue({id:'job-1',operation:'price',platform:'mercadolibre',status:'queued',created_at:'',updated_at:'',target_id:'remote-1',request:{},result:{}})
    render();await flushPromises();await click('管理 →');await click('调整价格')
    await wrapper!.get('input[inputmode="decimal"]').setValue('18.25')
    await click('预览变更');expect(onlineAction).not.toHaveBeenCalled()
    await click('确认提交')
    expect(onlineAction).toHaveBeenCalledWith('change',expect.objectContaining({listing_id:'remote-1',version:'v1',scope_id:'global',operation:'price',changes:{amount:'18.25',currency:'USD'}}))
  })
  it('结果未知只提供回读，不提供重试',async()=>{
    const page=response();page.jobs=[{id:'unknown',operation:'price',platform:'mercadolibre',status:'outcome_unknown',created_at:'',updated_at:'',target_id:'remote-1',request:{},result:{error:'平台可能已接受'}}]
    vi.mocked(fetchOnlineProducts).mockResolvedValue(page)
    render();await flushPromises();await click('操作记录');await click('查看详情')
    expect(wrapper!.text()).toContain('查询平台结果');expect(wrapper!.text()).not.toContain('仅重试失败项')
  })
  it('网络失败保留错误而不伪装为空店铺',async()=>{
    vi.mocked(fetchOnlineProducts).mockRejectedValue(new Error('授权读取失败'))
    render();await flushPromises();expect(wrapper!.get('[role="alert"]').text()).toContain('授权读取失败')
    expect(wrapper!.text()).not.toContain('店铺同步完成，暂无商品')
  })
  it('卸载后的迟到响应不会复活轮询',async()=>{
    let resolve!: (value:OnlinePage)=>void
    vi.mocked(fetchOnlineProducts).mockReturnValue(new Promise(r=>{resolve=r}))
    render();wrapper!.unmount();wrapper=undefined;resolve(response());await flushPromises();await vi.advanceTimersByTimeAsync(30000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
  })
})
