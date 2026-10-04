import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import OnlineProductsPanel from '../OnlineProductsPanel.vue'
import { fetchOnlineDetail, fetchOnlineProducts, fetchOnlineSourceImages, onlineAction, refreshOnlineStatus, type OnlineJob, type OnlineListing, type OnlinePage } from '@/api/onlineProducts'

vi.mock('@/api/onlineProducts', () => ({ fetchOnlineDetail: vi.fn(), fetchOnlineProducts: vi.fn(), fetchOnlineSourceImages: vi.fn(), onlineAction: vi.fn(), refreshOnlineStatus: vi.fn() }))
const item: OnlineListing = {
  id: 'remote-1', platform: 'mercadolibre', account_id: 'shop-1', remote_id: 'CBT123', model: 'traditional_global_items',
  details_state: 'ready', buyer_links: [], title: '店铺同步商品', seller_sku: 'seller-sku', thumbnail: '', raw_status: 'active', sale_state: 'active', raw_sub_status: [], version: 'v1', synced_at: '', status_checked_at: '', errors: [], desired_sale_state: '',
  platform_issues: [], prices: [{id:'global',label:'全局基础价',amount:'12',currency:'USD',kind:'base_price',writable:true,reason:''}], stocks: [], markets: [], content: {title:'店铺同步商品'},
  capabilities: {price:{enabled:true,fields:[],scope:'全局',reason:''},stock:{enabled:false,fields:[],scope:'',reason:'未取得库存位置'},content:{enabled:true,fields:['title'],scope:'全局',reason:''},sale_state:{enabled:true,fields:[],scope:'全局',reason:''}},
}
function response(): OnlinePage { return {items:[item],groups:[{id:'single-1',title:item.title,kind:'single',item_ids:[item.id],total_count:1,feedback_summary:{affected_sku_count:0,error_count:0,warning_count:0}}],total:1,listing_total:1,page:1,per_page:25,account_id:'shop-1',store_name:'当前店铺',state:'ready',markets:[],statuses:['active'],summary:{total:1,active:1,paused:0,attention:0},latest_sync:null,jobs:[]} }
function groupedResponse(): OnlinePage {
  const variants = Array.from({length:30}, (_, i) => ({...item, id:`variant-${i}`, seller_sku:`sku-${i}`}))
  return {...response(),items:variants,listing_total:30,groups:[{id:'group-1',title:'组合商品名称',kind:'group',item_ids:variants.map(row=>row.id),total_count:30,feedback_summary:{affected_sku_count:0,error_count:0,warning_count:0}}]}
}
let wrapper: VueWrapper | undefined
beforeEach(() => {setActivePinia(createPinia());vi.useFakeTimers(); vi.clearAllMocks();vi.mocked(fetchOnlineProducts).mockResolvedValue(response());vi.mocked(fetchOnlineDetail).mockResolvedValue(item)})
afterEach(() => {wrapper?.unmount();wrapper=undefined;document.body.innerHTML='';vi.useRealTimers()})
function render(){wrapper=mount(OnlineProductsPanel,{attachTo:document.body,global:{stubs:{Teleport:true}}});return wrapper}
async function click(text:string){const button=wrapper!.findAll('button').find(b=>b.text()===text);expect(button, text).toBeDefined();await button!.trigger('click');await flushPromises()}

describe('在线商品页面',()=>{
  it('直接展示属性名称和平台反馈，刷新后清除已消失的警告且保持在售', async () => {
    const row: OnlineListing = {...item, platform:'yandex', raw_status:'PUBLISHED', raw_sub_status:['HAS_CARD_CAN_UPDATE_PROCESSING'],
      content:{attributes:[{id:'57046341',parameterId:57046341,name:'Другие параметры',value:'Материал: Плюш\nТип: Игрушка'}]},
      platform_issues:[{severity:'warning',source:'card',code:'',message:'Не доставляется',comment:'Проверьте размеры упаковки'},
        {severity:'error',source:'card',code:'FORMAT',message:'Неверный формат',comment:'Используйте название: значение'}]}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[row]})
    vi.mocked(fetchOnlineDetail).mockResolvedValue(row)
    render(); await flushPromises(); await click('管理 →')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('1 个平台错误 · 1 个平台警告')
    const detail = wrapper!.get('[aria-label="商品详情"]')
    expect(detail.text()).toContain('在售')
    expect(detail.text()).toContain('卡片状态：卡片修改处理中')
    expect(detail.get('[data-testid="online-attributes"]').text()).toContain('Другие параметры')
    expect(detail.get('[data-testid="online-attributes"]').text()).toContain('属性编号：57046341')
    expect(detail.get('dd').text()).toBe('Материал: Плюш\nТип: Игрушка')
    expect(detail.get('[data-severity="warning"]').text()).toContain('Проверьте размеры упаковки')
    expect(detail.get('[data-severity="error"]').text()).toContain('Используйте название: значение')
    expect(detail.get('[data-testid="platform-feedback"]').text()).toContain('现有反馈可能来自上次处理')
    const next = {...row,platform_issues:[],raw_sub_status:['HAS_CARD_CAN_UPDATE'],status_checked_at:'2026-10-03T01:00:00Z'}
    vi.mocked(refreshOnlineStatus).mockResolvedValue(next)
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[next]})
    await detail.get('[data-testid="detail-refresh-status"]').trigger('click'); await flushPromises()
    expect(wrapper!.find('[data-severity="warning"]').exists()).toBe(false)
    expect(wrapper!.get('[data-testid="platform-feedback"]').text()).toContain('当前记录没有具体错误或警告')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('在售')
    expect(wrapper!.get('[data-testid="online-attributes"]').text()).toContain('Другие параметры')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('属性名称查询失败时展示原因并保留原值', async () => {
    vi.mocked(fetchOnlineDetail).mockResolvedValue({...item,content:{attributes:[{id:'1',value:'Плюш'}],attribute_names_error:'类目查询超时'}})
    render(); await flushPromises(); await click('管理 →')
    const attributes = wrapper!.get('[data-testid="online-attributes"]')
    expect(attributes.text()).toContain('类目查询超时')
    expect(attributes.text()).toContain('名称暂未取得')
    expect(attributes.get('dd').text()).toBe('Плюш')
  })
  it('选图排序后从预览返回保留编辑，提交仅包含资产身份', async () => {
    const row: OnlineListing = {...item, content: {pictures: [{id:'existing',url:'https://images.example.com/existing.jpg'}]},
      capabilities:{...item.capabilities,content:{enabled:true,fields:['pictures'],scope:'全局',reason:''}}}
    vi.mocked(fetchOnlineDetail).mockResolvedValue(row)
    vi.mocked(fetchOnlineSourceImages).mockResolvedValue({ok:true,local_product_id:'product',local_draft_id:'draft',reason:'',images:[
      {asset_id:'asset',fingerprint:'a'.repeat(64),preview_url:'/file?path=image',existing_picture_id:'',existing_url:''},
    ]})
    vi.mocked(onlineAction).mockResolvedValue({id:'job',operation:'content',platform:'mercadolibre',status:'queued',created_at:'',updated_at:'',target_id:row.id,request:{},result:{}})
    wrapper=mount(OnlineProductsPanel,{attachTo:document.body});
    const screen = new DOMWrapper(document.body)
    async function click(text: string) {await screen.findAll('button').find(button=>button.text()===text)!.trigger('click'); await flushPromises()}
    await flushPromises(); await click('管理 →'); await click('编辑内容')
    await screen.get('input[value="pictures"]').setValue(true)
    await click('从源草稿添加图片'); await screen.get('[aria-label="选择源草稿图片 1"]').trigger('click')
    await click('添加选中图片（1）'); await screen.get('[aria-label="左移图片 2"]').trigger('click')
    await click('预览变更'); await click('返回编辑')
    expect(screen.findAll('[data-testid="target-picture"]')).toHaveLength(2)
    expect(screen.get<HTMLInputElement>('input[value="pictures"]').element.checked).toBe(true)
    await click('预览变更'); await click('确认提交')
    expect(onlineAction).toHaveBeenCalledWith('change',expect.objectContaining({changes:{pictures:[
      {asset_id:'asset',fingerprint:'a'.repeat(64)}, {id:'existing',url:'https://images.example.com/existing.jpg'},
    ]}}))
  })
  it.each(['waiting_confirmation', 'outcome_unknown', 'confirmed'])('任务 %s 不维持定时轮询', async status => {
    const job: OnlineJob = {id:'waiting',operation:'price',platform:'mercadolibre',status,target_id:item.id,created_at:'',updated_at:'',request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
    render(); await flushPromises()
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
  })
  it('页面隐藏时停止活跃任务轮询，恢复可见后读取一次', async () => {
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    try {
      const job: OnlineJob = {id:'running',operation:'sync',platform:'mercadolibre',status:'running',target_id:'*',created_at:'',updated_at:'',request:{},result:{}}
      vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
      render(); await flushPromises()
      hidden.mockReturnValue(true)
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(60000)
      expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
      hidden.mockReturnValue(false)
      document.dispatchEvent(new Event('visibilitychange'))
      await flushPromises()
      expect(fetchOnlineProducts).toHaveBeenCalledTimes(2)
    } finally { hidden.mockRestore() }
  })
  it('首次自动确认到期后只读取一次本地结果，不推进平台任务', async () => {
    const job: OnlineJob = {id:'submitted',operation:'price',platform:'mercadolibre',status:'submitted',target_id:item.id,created_at:'',updated_at:'',request:{},result:{automatic_confirmation_pending:true,next_confirmation_at:Date.now()/1000+120}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
    render(); await flushPromises()
    await vi.advanceTimersByTimeAsync(120000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(2)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('单件刷新只查询目标状态，同时更新列表和详情并保留完整同步时间',async()=>{
    const old={...item,synced_at:'2026-09-20T01:00:00Z'}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[old]})
    vi.mocked(fetchOnlineDetail).mockResolvedValue(old)
    render();await flushPromises();await click('管理 →')
    const next={...old,raw_status:'paused',sale_state:'paused',status_checked_at:'2026-09-27T02:00:00Z',version:'v2'}
    vi.mocked(refreshOnlineStatus).mockResolvedValue(next)
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[next],summary:{total:1,active:0,paused:1,attention:0}})
    await wrapper!.get('[data-testid="detail-refresh-status"]').trigger('click');await flushPromises()
    expect(refreshOnlineStatus).toHaveBeenCalledTimes(1)
    expect(refreshOnlineStatus).toHaveBeenCalledWith(item.id)
    expect(onlineAction).not.toHaveBeenCalled()
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('已停售')
    const detail=wrapper!.get('[aria-label="商品详情"]').text()
    expect(detail).toContain('已停售')
    expect(detail).toContain('2026/9/20')
    expect(detail).toContain('状态查询：2026/9/27')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('USD 12')
  })
  it('查询中禁止重复提交，失败保留原数据且错误不会被列表定时读取清除',async()=>{
    let reject!: (reason: Error)=>void
    vi.mocked(refreshOnlineStatus).mockReturnValue(new Promise((_, fail)=>{reject=fail}))
    render();await flushPromises()
    const button=wrapper!.get('[data-testid="refresh-status"]')
    await button.trigger('click');await button.trigger('click')
    expect(refreshOnlineStatus).toHaveBeenCalledTimes(1)
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toBe('查询中…')
    reject(new Error('平台查询超时，原数据已保留'));await flushPromises()
    expect(button.attributes('disabled')).toBeUndefined()
    await vi.advanceTimersByTimeAsync(15000);await flushPromises()
    expect(wrapper!.text()).toContain('平台查询超时，原数据已保留')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('在售')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('切换平台后忽略上个平台的状态响应',async()=>{
    let resolve!: (value:OnlineListing)=>void
    vi.mocked(refreshOnlineStatus).mockReturnValue(new Promise(done=>{resolve=done}))
    render();await flushPromises()
    await wrapper!.get('[data-testid="refresh-status"]').trigger('click')
    await click('Yandex Market')
    resolve({...item,raw_status:'paused',sale_state:'paused'});await flushPromises()
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('在售')
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(2)
  })
  it('刷新之前发出的列表旧响应不能覆盖新状态',async()=>{
    const backgroundJob:OnlineJob={id:'other',operation:'price',platform:'mercadolibre',status:'running',target_id:'other-item',created_at:'',updated_at:'',request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[backgroundJob]})
    render();await flushPromises()
    let stale!: (value:OnlinePage)=>void
    vi.mocked(fetchOnlineProducts).mockReturnValueOnce(new Promise(done=>{stale=done}))
    await vi.advanceTimersByTimeAsync(5000)
    const next={...item,raw_status:'paused',sale_state:'paused',status_checked_at:'2026-09-27T02:00:00Z'}
    vi.mocked(refreshOnlineStatus).mockResolvedValue(next)
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[next]})
    await wrapper!.get('[data-testid="refresh-status"]').trigger('click');await flushPromises()
    stale(response());await flushPromises()
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('已停售')
  })
  it.each(['queued','running','waiting_confirmation','outcome_unknown'])('任务 %s 时按范围控制刷新按钮',async(jobStatus)=>{
    const job:OnlineJob={id:'j1',operation:'sale_state',platform:'mercadolibre',status:jobStatus,target_id:item.id,created_at:'',updated_at:'',request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
    render();await flushPromises()
    expect(wrapper!.get('[data-testid="refresh-status"]').attributes('disabled') !== undefined).toBe(['queued','running'].includes(jobStatus))
  })
  it('详情内按下并拖到遮罩松开不会误关，真正点击遮罩才关闭',async()=>{
    render();await flushPromises();await click('管理 →')
    const pointer={button:0,pointerId:1,isPrimary:true}
    const overlay=wrapper!.get('.online-overlay')
    await wrapper!.get('[aria-label="商品详情"] h3').trigger('pointerdown',pointer)
    await overlay.trigger('pointerup',pointer)
    // 浏览器会将跨元素按下/松开合成共同祖先上的 click。
    await overlay.trigger('click')
    expect(wrapper!.find('[aria-label="商品详情"]').exists()).toBe(true)
    await overlay.trigger('pointerdown',pointer)
    await overlay.trigger('pointerup',pointer)
    expect(wrapper!.find('[aria-label="商品详情"]').exists()).toBe(false)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('调价框拖选到外部、反向拖入或手势取消均保留输入，关闭只影响当前弹窗',async()=>{
    render();await flushPromises();await click('管理 →');await click('调整价格')
    const pointer={button:0,pointerId:1,isPrimary:true}
    const overlay=wrapper!.get('.online-modal-layer')
    const input=wrapper!.get('input[inputmode="decimal"]')
    await input.setValue('18.25')
    await input.trigger('pointerdown',pointer)
    await overlay.trigger('pointerup',pointer)
    await overlay.trigger('click')
    expect(wrapper!.get<HTMLInputElement>('input[inputmode="decimal"]').element.value).toBe('18.25')
    await overlay.trigger('pointerdown',pointer)
    await input.trigger('pointerup',pointer)
    await overlay.trigger('click')
    expect(wrapper!.find('.online-modal-layer').exists()).toBe(true)
    await overlay.trigger('pointerdown',pointer)
    await overlay.trigger('pointercancel',pointer)
    await overlay.trigger('pointerup',pointer)
    await overlay.trigger('click')
    expect(wrapper!.find('.online-modal-layer').exists()).toBe(true)
    await overlay.trigger('pointerdown',pointer)
    await overlay.trigger('pointerup',pointer)
    expect(wrapper!.find('.online-modal-layer').exists()).toBe(false)
    expect(wrapper!.find('[aria-label="商品详情"]').exists()).toBe(true)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('提交中的弹窗仍禁止通过遮罩关闭',async()=>{
    vi.mocked(onlineAction).mockReturnValue(new Promise(()=>{}))
    render();await flushPromises();await click('管理 →');await click('调整价格')
    await wrapper!.get('input[inputmode="decimal"]').setValue('18.25')
    await click('预览变更');await click('确认提交')
    const overlay=wrapper!.get('.online-modal-layer')
    const pointer={button:0,pointerId:1,isPrimary:true}
    await overlay.trigger('pointerdown',pointer)
    await overlay.trigger('pointerup',pointer)
    expect(wrapper!.find('.online-modal-layer').exists()).toBe(true)
    expect(wrapper!.text()).toContain('正在提交…')
    expect(onlineAction).toHaveBeenCalledTimes(1)
  })
  it('组合默认折叠，展开后按 SKU 管理，收起不会提交商品修改',async()=>{
    vi.mocked(fetchOnlineProducts).mockResolvedValue(groupedResponse())
    render();await flushPromises()
    expect(wrapper!.findAll('[data-testid="online-group"]')).toHaveLength(1)
    expect(wrapper!.get('[data-testid="online-group"]').findAll('button')).toHaveLength(1)
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(0)
    const toggle=wrapper!.get('[data-testid="toggle-group"]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper!.text()).toContain('30 个 SKU')
    expect(wrapper!.findAll('button').find(b=>b.text()==='下一页')!.attributes('disabled')).toBeDefined()
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(30)
    await wrapper!.findAll('[data-testid="online-listing"]')[1].get('button').trigger('click');await flushPromises()
    expect(fetchOnlineDetail).toHaveBeenCalledWith('variant-1')
    await click('关闭 ×');await toggle.trigger('click')
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(0)
    await toggle.trigger('click')
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(30)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('轮询保留展开状态，切换平台重新折叠',async()=>{
    vi.mocked(fetchOnlineProducts).mockResolvedValue(groupedResponse())
    render();await flushPromises();await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    await vi.advanceTimersByTimeAsync(15000);await flushPromises()
    expect(wrapper!.get('[data-testid="toggle-group"]').attributes('aria-expanded')).toBe('true')
    await click('Yandex Market')
    expect(wrapper!.get('[data-testid="toggle-group"]').attributes('aria-expanded')).toBe('false')
  })
  it('部分 SKU 命中筛选时保留父节点，并区分命中数与组合总数',async()=>{
    const result=groupedResponse()
    result.items=result.items.slice(0,1);result.listing_total=1;result.groups[0].item_ids=[result.items[0].id]
    vi.mocked(fetchOnlineProducts).mockResolvedValue(result)
    render();await flushPromises()
    expect(wrapper!.get('[data-testid="online-group"]').text()).toContain('当前筛选匹配 1 / 30 个 SKU')
    await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(1)
  })
  it('组合折叠时显示整个组合的反馈，筛选隐藏异常 SKU 也不会漏报',async()=>{
    const result=groupedResponse()
    result.items=result.items.slice(0,1);result.listing_total=1;result.groups[0].item_ids=[result.items[0].id]
    result.groups[0].feedback_summary={affected_sku_count:2,error_count:2,warning_count:3}
    vi.mocked(fetchOnlineProducts).mockResolvedValue(result)
    render();await flushPromises()
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(0)
    const feedback=wrapper!.get('[data-testid="group-feedback"]')
    expect(feedback.text()).toContain('整个组合：2 个 SKU 有平台反馈')
    expect(feedback.text()).toContain('2 条平台错误')
    expect(feedback.text()).toContain('3 条平台警告')
    await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    expect(wrapper!.findAll('[data-testid="online-listing"]')).toHaveLength(1)
    expect(wrapper!.get('[data-testid="group-feedback"]').text()).toContain('3 条平台警告')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('SKU 刷新后立即调整父行提示，列表查询失败时仍保留隐藏 SKU 的反馈',async()=>{
    const result=groupedResponse()
    result.items=result.items.slice(0,1);result.listing_total=1;result.groups[0].item_ids=[result.items[0].id]
    result.items[0].platform_issues=[{severity:'warning',source:'card',code:'',message:'配送警告',comment:''}]
    result.groups[0].feedback_summary={affected_sku_count:2,error_count:2,warning_count:2}
    vi.mocked(fetchOnlineProducts).mockResolvedValue(result)
    render();await flushPromises();await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    vi.mocked(refreshOnlineStatus).mockResolvedValue({...result.items[0],platform_issues:[]})
    vi.mocked(fetchOnlineProducts).mockRejectedValueOnce(new Error('列表查询失败'))
    await wrapper!.get('[data-testid="refresh-status"]').trigger('click');await flushPromises()
    const feedback=wrapper!.get('[data-testid="group-feedback"]')
    expect(feedback.text()).toContain('整个组合：1 个 SKU 有平台反馈')
    expect(feedback.text()).toContain('2 条平台错误')
    expect(feedback.text()).toContain('1 条平台警告')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).not.toContain('平台警告')
    expect(onlineAction).not.toHaveBeenCalled()
  })
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
  it('已有同步时直接查看阶段进度，不再次提交同步', async () => {
    const job = {id:'sync-1', operation:'sync', platform:'mercadolibre', status:'running', created_at:'', updated_at:'', target_id:'*', request:{},
      result:{phase:'details',discovery_complete:true,discovered:201,completed:100,failed:1}} as const
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),latest_sync:job,jobs:[job]})
    render();await flushPromises()
    expect(wrapper!.text()).toContain('正在补齐详情，已处理 101 / 201 件')
    await click('查看同步进度')
    expect(wrapper!.find('[aria-label="同步商品"]').exists()).toBe(false)
    expect(wrapper!.text()).toContain('正在补齐详情，已处理 101 / 201 件')
    expect(wrapper!.text()).not.toContain('网络超时后平台可能已完成修改')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it.each(['pending','failed'] as const)('详情 %s 时可查看记录但不能发起修改', async (state) => {
    const row = {...item, details_state:state, errors:state==='failed'?['价格读取超时']:[]}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[row]})
    vi.mocked(fetchOnlineDetail).mockResolvedValue(row)
    render();await flushPromises()
    expect(wrapper!.text()).toContain(state==='pending'?'详情同步中':'详情同步失败')
    await click('管理 →')
    const priceButton = wrapper!.findAll('button').find(b=>b.text()==='调整价格')!
    expect(priceButton.attributes('disabled')).toBeDefined()
    expect(onlineAction).not.toHaveBeenCalled()
  })

  it('部分失败的同步停止显示进行中，目录不完整时明确提示', async () => {
    const job = {id:'sync-1', operation:'sync', platform:'mercadolibre', status:'partial', created_at:'', updated_at:'', target_id:'*', request:{},
      result:{phase:'catalog',discovery_complete:false,discovered:100,completed:0,failed:100}} as const
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),latest_sync:job,jobs:[job]})
    render();await flushPromises()
    expect(wrapper!.text()).toContain('目录尚未读取完整')
    expect(wrapper!.text()).not.toContain('正在读取商品目录')
    expect(wrapper!.text()).not.toContain('正在补齐详情')
  })

})
