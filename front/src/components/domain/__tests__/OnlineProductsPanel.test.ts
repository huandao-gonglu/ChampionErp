import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { apiClient } from '@/api/client'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import OnlineProductsPanel from '../OnlineProductsPanel.vue'
import OzonExportDialog from '../OzonExportDialog.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchYandexShopNames, refreshOnlineSales, fetchOnlineDetail, fetchOnlineProducts, fetchOnlineSourceImages, onlineAction, previewOnlineBatch, submitOnlineBatch, refreshOnlineDetail, refreshOnlineStatus, type OnlineJob, type OnlineListing, type OnlinePage } from '@/api/onlineProducts'

vi.mock('@/api/onlineProducts', () => ({ fetchYandexShopNames: vi.fn(), refreshOnlineSales: vi.fn(), fetchOnlineDetail: vi.fn(), fetchOnlineProducts: vi.fn(), fetchOnlineSourceImages: vi.fn(), onlineAction: vi.fn(), previewOnlineBatch:vi.fn(), submitOnlineBatch:vi.fn(), refreshOnlineDetail: vi.fn(), refreshOnlineStatus: vi.fn() }))
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
function yandexItem(overrides: Partial<OnlineListing> = {}): OnlineListing {
  return {...item, platform:'yandex', model:'business_offer', archived:false,
    stocks:[{id:'warehouse',label:'主仓',quantity:7,writable:true,reason:''}],
    capabilities:{...item.capabilities,archive:{enabled:true,fields:['archived'],scope:'商家账号全部店铺',reason:''}}, ...overrides}
}
let wrapper: VueWrapper | undefined
const defaultAdapter = apiClient.defaults.adapter
beforeEach(() => {setActivePinia(createPinia());vi.useFakeTimers(); vi.resetAllMocks();vi.mocked(fetchYandexShopNames).mockResolvedValue({});vi.mocked(fetchOnlineProducts).mockResolvedValue(response());vi.mocked(fetchOnlineDetail).mockResolvedValue(item);vi.mocked(refreshOnlineDetail).mockImplementation(async id=>vi.mocked(fetchOnlineDetail)(id));vi.mocked(previewOnlineBatch).mockResolvedValue({items:[{listing_id:item.id,version:item.version,seller_sku:item.seller_sku,title:item.title,group_id:'single-1'}],errors:[],product_count:1,scope:'商家账号全部店铺'})})
afterEach(() => {wrapper?.unmount();wrapper=undefined;document.body.innerHTML='';apiClient.defaults.adapter=defaultAdapter;vi.useRealTimers()})
function render(){wrapper=mount(OnlineProductsPanel,{attachTo:document.body,global:{stubs:{Teleport:true,OzonExportDialog:true}}});return wrapper}
async function click(text:string){const button=wrapper!.findAll('button').find(b=>b.text()===text);expect(button, text).toBeDefined();await button!.trigger('click');await flushPromises()}
async function selectCatalog(value: 'current' | 'archived'){await wrapper!.get('[aria-label="商品目录"]').setValue(value);await flushPromises()}

describe('在线商品页面',()=>{
  it('价格列只显示有金额的报价，真实店铺名称放在提示中', async () => {
    const market = {id: '149191519', site_id: '149191519', name: '仓库甲', logistic_type: '', raw_status: '', price: null, currency: ''}
    const base = {...item.prices[0], id: 'business', amount: '48', currency: 'CNY', label: '账号基础价'}
    const independent = {...base, id: 'campaign:149191519', label: '仓库甲 独立价', amount: '50'}
    const row = yandexItem({markets: [market], prices: [base, independent, {...base, id: 'campaign:3', amount: null}]})
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), items: [row]})
    vi.mocked(fetchYandexShopNames).mockResolvedValue({'149191519': '春日商店'})
    render(); await flushPromises(); await click('Yandex Market')
    const cell = wrapper!.getComponent({name: 'OnlinePriceCell'})
    expect(cell.findAll('p').map(p => p.text())).toEqual(['CNY 48', 'CNY 50'])
    expect(cell.text()).not.toMatch(/149191519|仓库甲|春日商店|未知|价格范围/)
    await cell.get('button').trigger('focus')
    expect(cell.get('[role="tooltip"]').text()).toContain('春日商店：CNY 50')
    expect(cell.text()).not.toMatch(/149191519|仓库甲/)
    expect(fetchYandexShopNames).toHaveBeenCalledTimes(1)
  })

  it('通过任务队列查看历史，不再保留操作记录列表和详情弹窗', async () => {
    render(); await flushPromises()
    expect(wrapper!.text()).not.toContain('操作记录')
    expect(wrapper!.find('[data-testid="online-job-dialog"]').exists()).toBe(false)
    await click('任务队列')
    expect(wrapper!.emitted('open-task')).toEqual([[]])
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('Yandex 保修修改预览原值与目标值，确认后才提交现有商品', async () => {
    const row = yandexItem({content: {guarantee_period: {timePeriod: 6, timeUnit: 'MONTH'}},
      capabilities: {...item.capabilities, content: {enabled: true, fields: ['guarantee_period'], scope: '账号全部店铺', reason: ''}}})
    vi.mocked(fetchOnlineDetail).mockResolvedValue(row)
    vi.mocked(onlineAction).mockResolvedValue({id: 'job', operation: 'content', platform: 'yandex', status: 'queued', created_at: '', updated_at: '', target_id: row.id, request: {}, result: {}})
    render(); await flushPromises(); await click('管理 →'); await click('编辑内容')
    await wrapper!.get('input[value="guarantee_period"]').setValue(true)
    await wrapper!.get('[aria-label="Yandex 保修时长"]').setValue('12')
    await click('预览变更')
    expect(wrapper!.text()).toContain('原：6 个月')
    expect(wrapper!.text()).toContain('新：12 个月')
    expect(onlineAction).not.toHaveBeenCalled()
    await click('确认提交')
    expect(onlineAction).toHaveBeenCalledWith('change', expect.objectContaining({listing_id: row.id, version: row.version,
      operation: 'content', changes: {guarantee_period: {timePeriod: '12', timeUnit: 'MONTH'}}}))
  })

  it('停售入口保留，单件归档与恢复只修改归档状态并切换目录', async () => {
    let row = yandexItem()
    vi.mocked(fetchOnlineProducts).mockImplementation(async params => {
      const visible = row.archived === (params.catalog === 'archived')
      return {...response(),items:visible ? [row] : [],groups:visible ? response().groups : [],total:visible ? 1 : 0}
    })
    vi.mocked(fetchOnlineDetail).mockImplementation(async () => row)
    vi.mocked(onlineAction).mockImplementation(async (_action, body) => {
      row = {...row,archived:(body.changes as {archived:boolean}).archived,raw_status:'ARCHIVED'}
      return {id:'archive-job',operation:'archive',platform:'yandex',status:'confirmed',target_id:row.id,created_at:'',updated_at:'',request:body,result:{}}
    })
    render(); await flushPromises(); await click('Yandex Market'); await click('管理 →')
    const sale = wrapper!.findAll('button').find(button => button.text()==='停售商品')!
    expect(sale.attributes('disabled')).toBeUndefined()
    await wrapper!.get('[aria-label="商品详情"]').findAll('button').find(button => button.text()==='归档商品')!.trigger('click')
    await click('预览变更'); await click('确认提交')
    expect(onlineAction).toHaveBeenLastCalledWith('change',expect.objectContaining({operation:'archive',scope_id:'business',changes:{archived:true}}))
    expect(wrapper!.find('[data-testid="online-listing"]').exists()).toBe(false)
    await selectCatalog('archived')
    expect(fetchOnlineProducts).toHaveBeenLastCalledWith(expect.objectContaining({catalog:'archived'}))
    expect(useAiPageContextStore().current).toMatchObject({catalog:'archived'})
    expect(useAiPageContextStore().current?.listing_id).toBeUndefined()
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('已归档')
    await click('管理 →')
    expect(wrapper!.findAll('button').find(button => button.text()==='停售商品')!.attributes('disabled')).toBeDefined()
    await click('恢复归档'); await click('预览变更'); await click('确认提交')
    expect(onlineAction).toHaveBeenLastCalledWith('change',expect.objectContaining({operation:'archive',scope_id:'business',changes:{archived:false}}))
    expect(wrapper!.find('[data-testid="online-listing"]').exists()).toBe(false)
    await selectCatalog('current')
    expect(wrapper!.find('[data-testid="online-listing"]').exists()).toBe(true)
    expect(wrapper!.get('[data-testid="online-listing"]').text()).not.toContain('已归档')
    expect(row.stocks[0].quantity).toBe(7)
    expect(onlineAction).toHaveBeenCalledTimes(2)
  })
  it.each([
    ['archive',{archived:true},'归档所选商品'],
    ['archive',{archived:false},'恢复所选归档'],
    ['sale_state',{state:'paused'},'停售所选商品'],
    ['sale_state',{state:'active'},'恢复所选商品销售'],
  ] as const)('多商品 %s %j 只发送一次批量请求，网络失败重试保留幂等键', async (operation,changes,title) => {
    const data = groupedResponse()
    data.items = data.items.slice(0,2).map(row=>yandexItem({id:row.id,seller_sku:row.seller_sku}))
    data.groups = data.items.map((row,index)=>({...data.groups[0],id:`group-${index}`,item_ids:[row.id],total_count:2}))
    vi.mocked(fetchOnlineProducts).mockResolvedValue(data)
    const all = Array.from({length:4},(_,index)=>({listing_id:`all-${index}`,version:'v1',seller_sku:`all-sku-${index}`,title:'商品',group_id:`group-${Math.floor(index/2)}`}))
    vi.mocked(previewOnlineBatch).mockResolvedValue({items:all,errors:[],product_count:2,scope:operation==='archive'?'商家账号全部店铺':'当前店铺'})
    const jobs: OnlineJob[] = all.map(row=>({id:row.listing_id,operation,platform:'yandex',status:'queued',target_id:row.listing_id,created_at:'',updated_at:'',request:{},result:{}}))
    vi.mocked(submitOnlineBatch).mockRejectedValueOnce(new Error('提交连接中断')).mockResolvedValueOnce(jobs)
    render(); await flushPromises(); await click('Yandex Market')
    if ('archived' in changes && !changes.archived) await selectCatalog('archived')
    for (const checkbox of wrapper!.findAll('[data-testid="select-online-group"]')) await checkbox.setValue(true)
    await wrapper!.get('[data-testid="online-actions"]').trigger('click'); await click(title)
    expect(previewOnlineBatch).toHaveBeenCalledWith(expect.objectContaining({group_ids:['group-0','group-1'],listing_ids:['variant-0','variant-1'],operation,changes}))
    const dialog = () => wrapper!.get<HTMLDialogElement>('[data-testid="online-batch-dialog"]')
    expect(dialog().element.open).toBe(true)
    expect(dialog().text()).toContain('2 个商品、4 个 SKU')
    expect(dialog().text()).toContain('all-sku-3')
    await click(`确认${title}`)
    expect(submitOnlineBatch).toHaveBeenCalledWith(expect.objectContaining({items:all.map(({listing_id,version})=>({listing_id,version})),operation,changes}))
    expect(dialog().get('[role="alert"]').text()).toBe('提交连接中断')
    const original = vi.mocked(submitOnlineBatch).mock.calls[0]
    await click(`确认${title}`)
    expect(vi.mocked(submitOnlineBatch).mock.calls[1]).toEqual(original)
    expect(dialog().element.open).toBe(false)
    expect(submitOnlineBatch).toHaveBeenCalledTimes(2)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('父行下取消一个 SKU 后只预览剩余单件，不能继续包含整组', async () => {
    const data = groupedResponse()
    data.items = data.items.slice(0,2).map(row=>yandexItem({id:row.id,seller_sku:row.seller_sku}))
    data.groups[0].item_ids = data.items.map(row=>row.id)
    vi.mocked(fetchOnlineProducts).mockResolvedValue(data)
    render(); await flushPromises(); await click('Yandex Market')
    await wrapper!.get('[data-testid="select-online-group"]').setValue(true)
    await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    await wrapper!.findAll('[data-testid="select-online-listing"]')[0].setValue(false)
    await wrapper!.get('[data-testid="online-actions"]').trigger('click'); await click('停售所选商品')
    expect(previewOnlineBatch).toHaveBeenCalledWith(expect.objectContaining({group_ids:[],listing_ids:['variant-1']}))
  })
  it('预览列出不能处理的 SKU 并阻止提交，归档目录不提供停售', async () => {
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[yandexItem()]})
    vi.mocked(previewOnlineBatch).mockResolvedValue({items:[{listing_id:item.id,version:'v1',seller_sku:'SKU-1',title:'商品',group_id:'single'}],errors:[{listing_id:item.id,seller_sku:'SKU-1',error:'库存为零，不能请求恢复销售'}],product_count:1,scope:'当前店铺'})
    render(); await flushPromises(); await click('Yandex Market')
    await wrapper!.get('[data-testid="select-online-listing"]').setValue(true)
    await wrapper!.get('[data-testid="online-actions"]').trigger('click'); await click('恢复所选商品销售')
    expect(wrapper!.get('[data-testid="online-batch-dialog"] [role="alert"]').text()).toContain('SKU-1：库存为零')
    expect(wrapper!.findAll('button').find(button=>button.text()==='确认恢复所选商品销售')!.attributes('disabled')).toBeDefined()
    await wrapper!.get('[aria-label="关闭恢复所选商品销售"]').trigger('click'); await selectCatalog('archived')
    await wrapper!.get('[data-testid="online-actions"]').trigger('click')
    expect(wrapper!.get('#online-product-actions').text()).not.toContain('停售')
    expect(submitOnlineBatch).not.toHaveBeenCalled()
  })
  it('批量归档弹窗内部拖出不关闭，遮罩点击可关闭，提交期间保持打开', async () => {
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[yandexItem()]})
    render(); await flushPromises(); await click('Yandex Market')
    async function open() {
      await wrapper!.get('[data-testid="select-online-listing"]').setValue(true)
      await wrapper!.get('[data-testid="online-actions"]').trigger('click')
      await click('归档所选商品')
    }
    await open()
    const dialog = () => wrapper!.get<HTMLDialogElement>('[data-testid="online-batch-dialog"]')
    const pointer = {isPrimary:true,button:0,pointerId:1}
    await dialog().get('h3').trigger('pointerdown',pointer); await dialog().trigger('pointerup',pointer)
    expect(dialog().element.open).toBe(true)
    await dialog().trigger('pointerdown',pointer); await dialog().trigger('pointerup',pointer); await flushPromises()
    expect(dialog().element.open).toBe(false)
    await open()
    expect(dialog().element.open).toBe(true)
    let resolve!: (jobs:OnlineJob[])=>void
    vi.mocked(submitOnlineBatch).mockReturnValueOnce(new Promise(done=>{resolve=done}))
    await click('确认归档所选商品')
    expect(submitOnlineBatch).toHaveBeenCalledTimes(1)
    expect(dialog().text()).toContain('正在提交…')
    await dialog().trigger('pointerdown',pointer); await dialog().trigger('pointerup',pointer); await flushPromises()
    expect(wrapper!.findAllComponents(WorkspaceDialog).find(component=>component.props('title')==='归档所选商品')!.props()).toMatchObject({open:true,closeDisabled:true})
    resolve([{id:'batch-job',operation:'archive',platform:'yandex',status:'queued',target_id:item.id,created_at:'',updated_at:'',request:{},result:{}}])
    await flushPromises()
    expect(dialog().element.open).toBe(false)
  })
  it('冲突请求直达任务队列对应任务，不读取旧操作详情或重发修改', async () => {
    const actual = await vi.importActual<typeof import('@/api/onlineProducts')>('@/api/onlineProducts')
    vi.mocked(onlineAction).mockImplementationOnce(actual.onlineAction)
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      expect(config.method).toBe('post')
      expect(config.url).toBe('/api/online-products/change')
      throw new AxiosError('Request failed with status code 409', 'ERR_BAD_REQUEST', config, undefined, {
        config, status:409, statusText:'Conflict', headers:{},
        data:{ok:false,error:'该商品已有未完成的修改',error_code:'ONLINE_CONFLICT',conflict_job_id:'blocking-job'},
      })
    })
    apiClient.defaults.adapter = adapter
    render(); await flushPromises(); await click('管理 →'); await click('调整价格')
    await wrapper!.get('input[inputmode="decimal"]').setValue('18.25')
    await click('预览变更'); await click('确认提交'); await click('查看对应任务')
    expect(wrapper!.emitted('open-task')).toEqual([['blocking-job']])
    expect(wrapper!.find('[data-testid="online-job-dialog"]').exists()).toBe(false)
    expect(onlineAction).toHaveBeenCalledTimes(1)
    expect(adapter).toHaveBeenCalledTimes(1)
  })
  it('普通提交错误不显示冲突任务链接', async () => {
    vi.mocked(onlineAction).mockRejectedValueOnce(new Error('网络错误'))
    render(); await flushPromises(); await click('管理 →'); await click('调整价格')
    await click('预览变更'); await click('确认提交')
    expect(wrapper!.get('[aria-label="调整价格"]').text()).toContain('网络错误')
    expect(wrapper!.find('[data-testid="online-conflict-operation"]').exists()).toBe(false)
  })
  it.each(['0', '25'])('手动输入库存 %s 可预览，确认后按绝对数量提交', async quantity => {
    vi.mocked(fetchOnlineDetail).mockResolvedValue({
      ...item,
      stocks: [{id:'warehouse-1',label:'主仓库',quantity:9100,writable:true,reason:''}],
      capabilities: {...item.capabilities,stock:{enabled:true,fields:['quantity'],scope:'主仓库',reason:''}},
    })
    vi.mocked(onlineAction).mockResolvedValue({id:'stock-job',operation:'stock',platform:'mercadolibre',status:'queued',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}})
    render(); await flushPromises(); await click('管理 →'); await click('修改库存')
    await wrapper!.get('input[type="number"]').setValue(quantity)
    await click('预览变更')
    expect(wrapper!.text()).not.toContain('trim is not a function')
    expect(onlineAction).not.toHaveBeenCalled()
    await click('确认提交')
    expect(onlineAction).toHaveBeenCalledWith('change', expect.objectContaining({
      listing_id:item.id,version:'v1',scope_id:'warehouse-1',operation:'stock',changes:{quantity:Number(quantity)},
    }))
  })
  it.each([0, null])('Yandex 当前库存为 %s 时允许向指定仓库设置库存', async quantity => {
    vi.mocked(fetchOnlineDetail).mockResolvedValue(yandexItem({
      stocks: [{id:'2412479',label:'GUOO-Economy',quantity,writable:true,reason:''}],
      capabilities: {...item.capabilities,stock:{enabled:true,fields:['quantity'],scope:'所选卖家仓',reason:''}},
    }))
    vi.mocked(onlineAction).mockResolvedValue({id:'stock-job',operation:'stock',platform:'yandex',status:'queued',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}})
    render(); await flushPromises(); await click('管理 →'); await click('修改库存')
    await wrapper!.get('input[type="number"]').setValue('10')
    await click('预览变更')
    expect(onlineAction).not.toHaveBeenCalled()
    await click('确认提交')
    expect(onlineAction).toHaveBeenCalledWith('change', expect.objectContaining({
      listing_id:item.id,version:'v1',scope_id:'2412479',operation:'stock',changes:{quantity:10},
    }))
  })
  it.each(['', '-1', '1.5'])('库存输入 %s 不允许预览或提交', async quantity => {
    vi.mocked(fetchOnlineDetail).mockResolvedValue({
      ...item,
      stocks: [{id:'warehouse-1',label:'主仓库',quantity:9100,writable:true,reason:''}],
      capabilities: {...item.capabilities,stock:{enabled:true,fields:['quantity'],scope:'主仓库',reason:''}},
    })
    render(); await flushPromises(); await click('管理 →'); await click('修改库存')
    await wrapper!.get('input[type="number"]').setValue(quantity)
    await click('预览变更')
    expect(wrapper!.text()).toContain('库存必须是非负整数')
    expect(wrapper!.findAll('button').some(button => button.text() === '确认提交')).toBe(false)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('仅在 Yandex 筛选栏提供操作菜单，单选后从统一入口导出', async () => {
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[{...item,platform:'yandex'}]})
    render(); await flushPromises()
    expect(wrapper!.find('[data-testid="online-actions"]').exists()).toBe(false)
    await click('Yandex Market')
    const action = wrapper!.get('[data-testid="online-actions"]')
    expect(action.element.closest('form')).not.toBeNull()
    await action.trigger('click')
    expect(wrapper!.get('[data-testid="online-export-ozon"]').attributes('disabled')).toBeDefined()
    await action.trigger('click')
    await wrapper!.get('[data-testid="select-online-listing"]').setValue(true)
    await action.trigger('click')
    await wrapper!.get('[data-testid="online-export-ozon"]').trigger('click')
    expect(wrapper!.findComponent(OzonExportDialog).props('listingIds')).toEqual(['remote-1'])
    expect(wrapper!.findComponent(OzonExportDialog).props('accountId')).toBe('shop-1')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).not.toContain('导出')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('组合勾选只选择当前筛选匹配成员，单件取消后显示半选', async () => {
    const data = groupedResponse()
    data.items = data.items.slice(0,2).map(row=>({...row,platform:'yandex'}))
    data.groups[0].item_ids = data.items.map(row=>row.id)
    vi.mocked(fetchOnlineProducts).mockResolvedValue(data)
    render(); await flushPromises(); await click('Yandex Market')
    await wrapper!.get('[data-testid="select-online-group"]').setValue(true)
    await wrapper!.get('[data-testid="online-actions"]').trigger('click')
    await wrapper!.get('[data-testid="online-export-ozon"]').trigger('click')
    expect(wrapper!.findComponent(OzonExportDialog).props('listingIds')).toEqual(['variant-0','variant-1'])
    wrapper!.findComponent(OzonExportDialog).vm.$emit('close'); await flushPromises()
    await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    await wrapper!.findAll('[data-testid="select-online-listing"]')[0].setValue(false)
    expect((wrapper!.get('[data-testid="select-online-group"]').element as HTMLInputElement).indeterminate).toBe(true)
    await wrapper!.get('[data-testid="online-actions"]').trigger('click')
    await wrapper!.get('[data-testid="online-export-ozon"]').trigger('click')
    expect(wrapper!.findComponent(OzonExportDialog).props('listingIds')).toEqual(['variant-1'])
  })
  it('筛选或平台切换清除选择，避免把旧列表的商品带入导出', async () => {
    render(); await flushPromises(); await click('Yandex Market')
    await wrapper!.get('[data-testid="select-online-listing"]').setValue(true)
    await wrapper!.get('form').trigger('submit'); await flushPromises()
    expect((wrapper!.get('[data-testid="select-online-listing"]').element as HTMLInputElement).checked).toBe(false)
    await wrapper!.get('[data-testid="select-online-listing"]').setValue(true)
    await click('Ozon'); await click('Yandex Market')
    expect((wrapper!.get('[data-testid="select-online-listing"]').element as HTMLInputElement).checked).toBe(false)
  })
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
    expect(detail.get('[data-testid="online-attributes"]').text()).not.toContain('属性编号')
    expect(detail.get('[data-testid="platform-details"]').text()).toContain('属性编号：57046341')
    expect(detail.get('[data-testid="platform-details"]').attributes('open')).toBeUndefined()
    expect(detail.get('dd').text()).toBe('Материал: Плюш\nТип: Игрушка')
    expect(detail.get('[data-severity="warning"]').text()).toContain('Проверьте размеры упаковки')
    expect(detail.get('[data-severity="error"]').text()).toContain('Используйте название: значение')
    expect(detail.get('[data-testid="platform-feedback"]').text()).toContain('现有反馈可能来自上次处理')
    const next = {...row,platform_issues:[],raw_sub_status:['HAS_CARD_CAN_UPDATE'],status_checked_at:'2026-10-03T01:00:00Z'}
    vi.mocked(refreshOnlineStatus).mockResolvedValue(next)
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[next]})
    await detail.get('[data-testid="detail-refresh-status"]').trigger('click'); await flushPromises()
    expect(wrapper!.find('[data-severity="warning"]').exists()).toBe(false)
    expect(wrapper!.find('[data-testid="platform-feedback"]').exists()).toBe(false)
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('在售')
    expect(wrapper!.get('[data-testid="online-attributes"]').text()).toContain('Другие параметры')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('缺货商品优先展示各价格和库存范围，正常卡片与原始码默认折叠', async () => {
    const row: OnlineListing = {...item,platform:'yandex',raw_status:'NO_STOCKS',sale_state:'unknown',raw_sub_status:['HAS_CARD_CAN_UPDATE'],
      stocks:[{id:'warehouse-1',label:'主仓库',quantity:0,writable:true,reason:''},{id:'warehouse-2',label:'备用仓库',quantity:null,writable:false,reason:'平台未提供'}],
      prices:[...item.prices,{id:'sale',label:'店铺售价',amount:'0',currency:'RUB',kind:'sale_price',writable:false,reason:''}],
      capabilities:{...item.capabilities,sale_state:{...item.capabilities.sale_state,reason:'隐藏 / 恢复仅影响当前店铺，恢复不绕过平台审核'}}}
    vi.mocked(fetchOnlineDetail).mockResolvedValue(row)
    render(); await flushPromises(); await click('管理 →')
    const detail = wrapper!.get('[aria-label="商品详情"]')
    const summary = detail.get('[data-testid="detail-sales-summary"]')
    expect(summary.text()).toContain('USD 12')
    expect(summary.text()).toContain('RUB 0')
    expect(summary.text()).toContain('0件')
    expect(summary.text()).toContain('主仓库')
    expect(summary.text()).toContain('备用仓库')
    expect(summary.text()).toContain('未知')
    expect(detail.text().indexOf('USD 12')).toBeLessThan(detail.text().indexOf('调整价格'))
    expect(detail.find('[data-testid="platform-feedback"]').exists()).toBe(false)
    expect(detail.get('[data-testid="platform-details"]').attributes('open')).toBeUndefined()
    const main = detail.element.cloneNode(true) as HTMLElement
    main.querySelector('[data-testid="platform-details"]')!.remove()
    expect(main.textContent).toContain('缺货')
    expect(main.textContent).not.toContain('NO_STOCKS')
    expect(main.textContent).not.toContain('卡片可更新')
    expect(main.textContent).not.toContain('恢复不绕过平台审核')
    await click('停售商品')
    expect(wrapper!.get('[aria-label="停售 / 恢复"]').text()).toContain('恢复不绕过平台审核')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('没有具体反馈时仍展示卡片处理中提示，卡片失败保留可读原因', async () => {
    const row = {...item,platform:'yandex' as const,raw_status:'PUBLISHED',raw_sub_status:['NO_CARD_PROCESSING']}
    vi.mocked(fetchOnlineDetail).mockResolvedValue(row)
    render(); await flushPromises(); await click('管理 →')
    expect(wrapper!.get('[data-testid="platform-feedback"]').text()).toContain('完成后请刷新状态')
    const next = {...row,raw_sub_status:['NO_CARD_ERRORS']}
    vi.mocked(refreshOnlineStatus).mockResolvedValue(next)
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[next]})
    await wrapper!.get('[data-testid="detail-refresh-status"]').trigger('click'); await flushPromises()
    expect(wrapper!.find('[data-testid="platform-feedback"]').exists()).toBe(false)
    const detail = wrapper!.get('[aria-label="商品详情"]')
    expect(detail.get('[role="status"]').text()).toContain('卡片创建失败')
    expect(detail.text()).toContain('在售')
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
  it('已结束提交的本地记录不再启动页面自动查询', async () => {
    const job: OnlineJob = {id:'submitted',operation:'price',platform:'mercadolibre',status:'submitted',target_id:item.id,created_at:'',updated_at:'',request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
    render(); await flushPromises()
    await vi.advanceTimersByTimeAsync(120000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchOnlineProducts).toHaveBeenCalledTimes(1)
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
    const variant = wrapper!.findAll('[data-testid="online-listing"]')[1]
    await variant.findAll('button').find(button => button.text() === '管理 →')!.trigger('click');await flushPromises()
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
  it('AI 背景保留最近操作的在线商品，切换平台和卸载后清理',async()=>{
    render();await flushPromises()
    const context=useAiPageContextStore()
    expect(context.current).toMatchObject({page:'onlineProducts',platform:'mercadolibre'})
    await click('管理 →')
    expect(context.current?.listing_id).toBe('remote-1')
    expect(context.current?.draft_id).toBeUndefined()
    await click('关闭 ×')
    expect(context.current?.listing_id).toBe('remote-1')
    await click('Yandex Market')
    expect(context.current?.platform).toBe('yandex')
    expect(context.current?.listing_id).toBeUndefined()
    expect(context.current?.group_id).toBeUndefined()
    wrapper!.unmount();wrapper=undefined
    expect(context.current).toBeNull()
  })
  it('展开组合、键盘聚焦成员和回到组合时更新背景，失焦仍保留定位',async()=>{
    vi.mocked(fetchOnlineProducts).mockResolvedValue(groupedResponse())
    render(); await flushPromises()
    const context = useAiPageContextStore()
    expect(context.current?.group_id).toBeUndefined()
    await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    expect(context.current).toMatchObject({group_id:'group-1'})
    expect(context.current?.listing_id).toBeUndefined()
    const row = wrapper!.findAll('[data-testid="online-listing"]')[5]
    await row.get('button').trigger('focusin')
    expect(context.current).toMatchObject({group_id:'group-1',listing_id:'variant-5'})
    await row.get('button').trigger('focusout')
    expect(context.snapshot()).toMatchObject({group_id:'group-1',listing_id:'variant-5'})
    await wrapper!.get('[data-testid="online-group"]').trigger('click')
    expect(context.current?.listing_id).toBeUndefined()
    expect(context.current?.group_id).toBe('group-1')
  })
  it('筛选立即撤销旧焦点，刷新保留有效焦点并清理消失的商品',async()=>{
    render(); await flushPromises()
    const context = useAiPageContextStore()
    await wrapper!.get('[data-testid="online-listing"]').trigger('click')
    document.dispatchEvent(new Event('visibilitychange')); await flushPromises()
    expect(context.current?.listing_id).toBe('remote-1')
    await wrapper!.get('form').trigger('submit')
    expect(context.current?.listing_id).toBeUndefined()
    await flushPromises()
    await wrapper!.get('[data-testid="online-listing"]').trigger('click')
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[],groups:[]})
    document.dispatchEvent(new Event('visibilitychange')); await flushPromises()
    expect(context.current?.listing_id).toBeUndefined()
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
    expect(wrapper!.find('[data-testid="online-listing"]').exists()).toBe(true)
    expect(wrapper!.text()).toContain('处理结果可在任务队列查看')
    await click('查看任务')
    expect(wrapper!.emitted('open-task')).toEqual([['job-1']])
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
  it.each(['current', 'archived'] as const)('同步确认和提交只包含当前 %s 目录，网络重试保留范围和提交键', async catalog => {
    const row = yandexItem({archived:catalog==='archived'})
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[row]})
    const job: OnlineJob = {id:'sync-catalog',operation:'sync',platform:'yandex',status:'queued',created_at:'',updated_at:'',target_id:'*',request:{catalog},result:{}}
    vi.mocked(onlineAction).mockRejectedValueOnce(new Error('网络中断')).mockResolvedValue(job)
    render();await flushPromises();await click('Yandex Market')
    if (catalog==='archived') await selectCatalog('archived')
    await click('↻ 同步商品')
    const dialog = wrapper!.get('[aria-label="同步商品"]')
    expect(dialog.text()).toContain(catalog==='archived' ? '仅同步当前账号的归档商品' : '仅同步当前账号的普通商品')
    expect(dialog.text()).not.toContain('和归档记录')
    await click('开始同步')
    const first = vi.mocked(onlineAction).mock.calls[0]
    expect(first).toEqual(['sync',expect.objectContaining({platform:'yandex',catalog})])
    expect(wrapper!.get('[aria-label="同步商品"]').text()).toContain('网络中断')
    await click('开始同步')
    expect(vi.mocked(onlineAction).mock.calls[1]).toEqual(first)
    expect(wrapper!.text()).toContain(catalog==='archived' ? '归档商品同步已排队' : '普通商品同步已排队')
  })
  it('另一目录正在同步时不展示为本目录进度，切换到对应目录才能查看', async () => {
    const job: OnlineJob = {id:'archived-sync',operation:'sync',platform:'yandex',status:'running',created_at:'',updated_at:'',target_id:'*',request:{catalog:'archived'},result:{}}
    vi.mocked(fetchOnlineProducts).mockImplementation(async params => ({...response(),jobs:[job],latest_sync:params.catalog==='archived'?job:null}))
    render();await flushPromises();await click('Yandex Market')
    expect(wrapper!.findAll('button').find(b=>b.text()==='其他目录同步中')!.attributes('disabled')).toBeDefined()
    expect(wrapper!.text()).not.toContain('最近同步 · 归档商品')
    await selectCatalog('archived')
    expect(wrapper!.text()).toContain('最近同步 · 归档商品')
    await click('查看同步进度')
    expect(wrapper!.emitted('open-task')).toEqual([['archived-sync']])
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('已有同步时直接查看阶段进度，不再次提交同步', async () => {
    const job = {id:'sync-1', operation:'sync', platform:'mercadolibre', status:'running', created_at:'', updated_at:'', target_id:'*', request:{},
      result:{phase:'details',discovery_complete:true,discovered:201,completed:100,failed:1}} as const
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),latest_sync:job,jobs:[job]})
    render();await flushPromises()
    expect(wrapper!.text()).toContain('正在补齐详情，已处理 101 / 201 件')
    await click('查看同步进度')
    expect(wrapper!.emitted('open-task')).toEqual([['sync-1']])
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

describe('Yandex 基础与单件详情', () => {
  it('折叠的组合显示价格区间和已知库存，后台按页刷新后显示零库存', async () => {
    const first = yandexItem({id:'sku-1', details_state:'unloaded'})
    const second = yandexItem({id:'sku-2', prices:[{...first.prices[0],amount:'18'}], stocks:[{...first.stocks[0],quantity:null}]})
    const page = {...groupedResponse(), account_id:'1:2', items:[first,second], sales_due_ids:[second.id]}
    page.groups[0].item_ids = [first.id, second.id]
    page.groups[0].total_count = 2
    let finish!: () => void
    vi.mocked(refreshOnlineSales).mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ok:true}) }))
    render(); await flushPromises()
    vi.mocked(fetchOnlineProducts).mockResolvedValue(page)
    await click('Yandex Market')
    expect(wrapper!.get('[data-testid="online-group"]').text()).toContain('USD 12–18')
    expect(wrapper!.get('[data-testid="online-group"]').text()).toContain('仅合计已知库存')
    expect(refreshOnlineSales).toHaveBeenCalledWith('1:2', ['sku-2'])
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...page, sales_due_ids:[], items:[first,{...second,stocks:[{...second.stocks[0],quantity:0}]}]})
    finish(); await flushPromises()
    expect(wrapper!.get('[data-testid="online-group"]').text()).not.toContain('仅合计已知库存')
    await wrapper!.get('[data-testid="toggle-group"]').trigger('click')
    expect(wrapper!.findAll('[data-testid="stock-summary"]')[1].text()).toBe('0')
    expect(refreshOnlineSales).toHaveBeenCalledTimes(1)
    expect(refreshOnlineDetail).not.toHaveBeenCalled()
  })

  it('列表和展开组合不读取详细数据，打开当前 SKU 才读取一次', async () => {
    const row = yandexItem({details_state:'unloaded',synced_at:'',basic_synced_at:'2026-11-01T01:00:00Z',prices:[],stocks:[],
      platform_issues:[{severity:'warning',source:'card',code:'W',message:'已有平台警告',comment:''}]})
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[row]})
    let done!: (row:OnlineListing)=>void
    vi.mocked(refreshOnlineDetail).mockReturnValueOnce(new Promise(resolve=>{done=resolve}))
    render(); await flushPromises(); await click('Yandex Market')
    expect(refreshOnlineDetail).not.toHaveBeenCalled()
    expect(wrapper!.text()).not.toContain('详细数据尚未读取')
    await click('管理 →')
    expect(refreshOnlineDetail).toHaveBeenCalledTimes(1)
    expect(refreshOnlineDetail).toHaveBeenCalledWith(row.id)
    expect(wrapper!.get('[data-testid="detail-loading"]').text()).toContain('当前商品详情')
    expect(wrapper!.get('[aria-label="商品详情"]').text()).toContain('已有平台警告')
    expect(wrapper!.get('[data-testid="detail-sales-summary"]').text()).toContain('尚未取得库存')
    await click('管理 →')
    expect(refreshOnlineDetail).toHaveBeenCalledTimes(1)
    done({...row,details_state:'ready',synced_at:'2026-11-01T01:01:00Z',stocks:[{id:'w',label:'当前仓库',quantity:9,writable:true,reason:''}]})
    await flushPromises()
    expect(wrapper!.find('[data-testid="detail-loading"]').exists()).toBe(false)
    expect(wrapper!.get('[data-testid="detail-sales-summary"]').text()).toContain('9')
    expect(onlineAction).not.toHaveBeenCalled()
  })

  it('详情读取失败保留旧价格与警告，不自动重试，可手动重新读取', async () => {
    const row = yandexItem({basic_synced_at:'2026-11-01T01:00:00Z',synced_at:'2026-10-31T01:00:00Z',
      platform_issues:[{severity:'warning',source:'card',code:'W',message:'旧警告仍保留',comment:''}]})
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[row]})
    vi.mocked(refreshOnlineDetail).mockRejectedValueOnce(new Error('库存连接中断')).mockResolvedValueOnce(row)
    render(); await flushPromises(); await click('Yandex Market'); await click('管理 →')
    expect(wrapper!.get('[data-testid="detail-error"]').text()).toContain('库存连接中断')
    expect(wrapper!.get('[data-testid="detail-sales-summary"]').text()).toContain('12')
    expect(wrapper!.get('[aria-label="商品详情"]').text()).toContain('旧警告仍保留')
    expect(wrapper!.text()).toContain('价格与库存为缓存')
    await vi.advanceTimersByTimeAsync(30000); await flushPromises()
    expect(refreshOnlineDetail).toHaveBeenCalledTimes(1)
    expect(wrapper!.findAll('button').find(button=>button.text()==='调整价格')!.attributes('disabled')).toBeDefined()
    await click('重新读取详情')
    expect(refreshOnlineDetail).toHaveBeenCalledTimes(2)
    expect(wrapper!.find('[data-testid="detail-error"]').exists()).toBe(false)
  })

  it('先前商品的迟到响应不能覆盖当前打开的商品', async () => {
    const first = yandexItem({id:'first'}), second = yandexItem({id:'second',title:'第二个商品'})
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),items:[first,second],groups:[
      {...response().groups[0],id:'one',item_ids:[first.id]}, {...response().groups[0],id:'two',item_ids:[second.id]},
    ]})
    const complete: Record<string,(row:OnlineListing)=>void> = {}
    vi.mocked(refreshOnlineDetail).mockImplementation(id=>new Promise(resolve=>{complete[id]=resolve}))
    render(); await flushPromises(); await click('Yandex Market')
    const manage = wrapper!.findAll('button').filter(button=>button.text()==='管理 →')
    await manage[0].trigger('click'); await flushPromises()
    await manage[1].trigger('click'); await flushPromises()
    complete.second(second); await flushPromises()
    complete.first({...first,title:'不应覆盖当前商品'}); await flushPromises()
    expect(wrapper!.get('[aria-label="商品详情"]').text()).toContain('第二个商品')
    expect(wrapper!.get('[aria-label="商品详情"]').text()).not.toContain('不应覆盖当前商品')
    expect(refreshOnlineDetail).toHaveBeenCalledTimes(2)
  })
})



function multiWarehouseRow(): OnlineListing {
  return yandexItem({account_id:'1:2',raw_status:'PUBLISHED',sale_state:'active',
    stocks:[{id:'economy',label:'Economy 仓',quantity:10,writable:true,reason:'',campaign_id:'2',raw_status:'NO_STOCKS'},
      {id:'express',label:'Express 仓',quantity:576,writable:true,reason:'',campaign_id:'3',raw_status:'PUBLISHED'}],
    markets:[{id:'2',site_id:'2',name:'Economy 仓',raw_status:'NO_STOCKS',logistic_type:'',price:null,currency:'CNY'},
      {id:'3',site_id:'3',name:'Express 仓',raw_status:'PUBLISHED',logistic_type:'',price:null,currency:'CNY'}],
    capabilities:{...item.capabilities,stock:{enabled:true,scope:'所选仓库',reason:'',fields:['quantity']}}})
}
const warehouseOptions = [{id:'economy',name:'Economy 仓',campaign_id:'2'},{id:'express',name:'Express 仓',campaign_id:'3'}]

it('列表库存只显示筛选范围的合计，保留管理入口并移除表中表', async () => {
  const row = multiWarehouseRow()
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),account_id:'1:2',items:[row],warehouses:warehouseOptions})
  vi.mocked(refreshOnlineDetail).mockResolvedValue(row)
  vi.mocked(onlineAction).mockResolvedValue({id:'stock-job',operation:'stock',platform:'yandex',status:'queued',target_id:row.id,created_at:'',updated_at:'',request:{},result:{}})
  render(); await flushPromises(); await click('Yandex Market')
  expect(wrapper!.get('[data-testid="stock-summary"]').text()).toBe('586')
  expect(wrapper!.find('[aria-label="各仓库库存"]').exists()).toBe(false)
  expect(wrapper!.get('[data-testid="online-listing"]').find('table').exists()).toBe(false)
  await wrapper!.get('[aria-label="仓库"]').setValue('express'); await flushPromises()
  expect(fetchOnlineProducts).toHaveBeenLastCalledWith(expect.objectContaining({warehouse_id:'express'}))
  expect(wrapper!.get('[data-testid="stock-summary"]').text()).toBe('576')
  await click('管理 →'); await click('修改库存')
  const dialog = wrapper!.get('[aria-label="修改库存"]')
  expect((dialog.get('select').element as HTMLSelectElement).value).toBe('express')
  await dialog.get('input[type="number"]').setValue('9')
  await click('预览变更'); await click('确认提交')
  expect(onlineAction).toHaveBeenLastCalledWith('change',expect.objectContaining({scope_id:'express',changes:{quantity:9}}))
})

it.each([
  [[null,576], '576', true],
  [[0,0], '0', false],
  [[null,null], '未知', false],
] as const)('库存 %j 正确汇总，未知值保留说明', async (quantities, total, partial) => {
  const row = multiWarehouseRow()
  row.stocks = row.stocks.map((stock,index)=>({...stock,quantity:quantities[index]}))
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),account_id:'1:2',items:[row],warehouses:warehouseOptions})
  render(); await flushPromises(); await click('Yandex Market')
  const cell = wrapper!.get('[data-testid="stock-summary"]')
  expect(cell.findAll('p')[0].text()).toBe(total)
  expect(cell.text().includes('部分仓库库存未知')).toBe(partial)
})

it('单仓查看及打开详情后，状态和库存仍按选中仓库展示', async () => {
  const row = multiWarehouseRow()
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),account_id:'1:2',items:[row],warehouses:warehouseOptions})
  vi.mocked(refreshOnlineDetail).mockResolvedValue(row)
  render(); await flushPromises(); await click('Yandex Market')
  expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('在售')
  await wrapper!.get('[aria-label="仓库"]').setValue('economy'); await flushPromises()
  expect(wrapper!.get('[data-testid="stock-summary"]').text()).toBe('10')
  expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('缺货')
  await click('管理 →')
  expect(wrapper!.get('[data-testid="stock-summary"]').text()).toBe('10')
  expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('缺货')
})

it('后台刷新可见商品价格库存时遇同步冲突静默让步，不弹出页面错误条', async () => {
  const row = yandexItem({id: 'sku-due', details_state: 'unloaded'})
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), account_id: '1:2', items: [row], sales_due_ids: [row.id]})
  vi.mocked(refreshOnlineSales).mockRejectedValueOnce(Object.assign(new Error('当前店铺有同步或修改进行中'), {raw: {error_code: 'ONLINE_CONFLICT'}}))
  render(); await flushPromises(); await click('Yandex Market')
  expect(refreshOnlineSales).toHaveBeenCalledTimes(1)
  expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
})

it('全量同步不误标为失败商品（0 件），仅失败项重试展示数量，同步完成不重复提示已处理', async () => {
  const fullJob: OnlineJob = {id: 'sync-full', operation: 'sync', platform: 'yandex', status: 'confirmed', created_at: '', updated_at: '2026-11-22T12:00:00Z', target_id: '*', request: {catalog: 'current', retry_count: 0}, result: {completed: 442, created: 0, updated: 442, failed: 0, discovery_complete: true}}
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), latest_sync: fullJob, jobs: [fullJob]})
  render(); await flushPromises(); await click('Yandex Market')
  const bar = wrapper!.get('[data-testid="online-sync-bar"]')
  expect(bar.text()).toContain('最近同步 · 普通商品 · 同步完成')
  expect(bar.text()).not.toContain('失败商品（0 件）')
  expect(bar.text()).not.toContain('已处理 442 件')

  const retryJob: OnlineJob = {...fullJob, id: 'sync-retry', status: 'running', request: {catalog: 'current', retry_count: 3}, result: {phase: 'basic', completed: 1, failed: 0}}
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), latest_sync: retryJob, jobs: [retryJob]})
  await selectCatalog('archived'); await selectCatalog('current')
  expect(wrapper!.get('[data-testid="online-sync-bar"]').text()).toContain('失败商品（3 件）')
})

it('多项价格读取异常在列表折成单行摘要并收入提示气泡，缩略图加载失败降级为占位块，同步栏展示清理数', async () => {
  const row = yandexItem({
    thumbnail: 'https://broken.trycloudflare.com/assets/missing.jpg',
    prices: [
      {id: 'business', label: '账号基础价（全部店铺）', amount: '200', currency: 'CNY', kind: 'base_price', writable: true, reason: '', cached_at: '2026-10-01T02:14:54Z', read_error: '平台未返回该 SKU 的账号基础价'},
      {id: 'campaign:1', label: '仓A 独立价', amount: null, currency: '', kind: 'sale_price', writable: false, reason: '店铺仅使用账号基础价', read_error: '平台未返回该 SKU 的店铺商品记录'},
      {id: 'campaign:2', label: '仓B 独立价', amount: null, currency: '', kind: 'sale_price', writable: false, reason: '店铺仅使用账号基础价', read_error: '平台未返回该 SKU 的店铺商品记录'},
    ],
  })
  const cleanJob: OnlineJob = {id: 'sync-clean', operation: 'sync', platform: 'yandex', status: 'confirmed', created_at: '', updated_at: '2026-11-22T12:00:00Z', target_id: '*', request: {catalog: 'current'}, result: {completed: 442, created: 0, updated: 442, removed: 3, moved: 1, failed: 0, discovery_complete: true}}
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), items: [row], latest_sync: cleanJob, jobs: [cleanJob]})
  render(); await flushPromises(); await click('Yandex Market')

  const bar = wrapper!.get('[data-testid="online-sync-bar"]')
  expect(bar.text()).toContain('已清理 3')
  expect(bar.text()).toContain('调整目录 1')

  const note = wrapper!.get('[data-testid="price-sales-note"]')
  expect(note.text()).toBe('沿用旧价 · 2 项未返回')
  expect(wrapper!.findAll('[data-testid="price-sales-note"]')).toHaveLength(1)
  const cell = wrapper!.getComponent({name: 'OnlinePriceCell'})
  await cell.get('button').trigger('focus')
  const tooltip = cell.get('[role="tooltip"]').text()
  expect(tooltip).toContain('账号基础价（全部店铺）沿用')
  expect(tooltip).toContain('仓A 独立价：平台未返回该 SKU 的店铺商品记录')

  const img = wrapper!.get('[data-testid="online-listing"] img')
  await img.trigger('error')
  expect(wrapper!.find('[data-testid="online-listing"] img').exists()).toBe(false)
  expect(wrapper!.find('[data-testid="thumbnail-placeholder"]').exists()).toBe(true)
})

it('三平台已有本地商品时均提供一键热数据快刷（mode=hot），空店铺隐藏按钮，状态条展示快刷进度', async () => {
  const row = yandexItem()
  const mlHotJob: OnlineJob = {
    id: 'sync-hot-ml', operation: 'sync', platform: 'mercadolibre', status: 'queued',
    created_at: '', updated_at: '2026-11-22T12:00:00Z', target_id: '*',
    request: {catalog: 'current', mode: 'hot'},
    result: {phase: 'hot', data_scope: 'hot', discovered: 1, completed: 0, failed: 0, discovery_complete: true},
  }
  const hotJob: OnlineJob = {
    id: 'sync-hot-1', operation: 'sync', platform: 'yandex', status: 'running',
    created_at: '', updated_at: '2026-11-22T12:00:00Z', target_id: '*',
    request: {catalog: 'current', mode: 'hot'},
    result: {phase: 'hot', data_scope: 'hot', discovered: 442, completed: 200, failed: 0, discovery_complete: true},
  }
  // 无本地商品时隐藏快刷按钮。
  vi.mocked(fetchOnlineProducts).mockResolvedValueOnce({...response(), items: [], groups: [], summary: {total: 0, active: 0, paused: 0, attention: 0}})
  render(); await flushPromises()
  expect(wrapper!.find('[data-testid="hot-refresh-btn"]').exists()).toBe(false)

  // Mercado Libre 已有商品时支持一键快刷。
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), summary: {total: 12, active: 10, paused: 2, attention: 0}})
  vi.mocked(onlineAction).mockResolvedValueOnce(mlHotJob)
  await click('Ozon'); await click('Mercado Libre')
  await wrapper!.get('[data-testid="hot-refresh-btn"]').trigger('click'); await flushPromises()
  expect(onlineAction).toHaveBeenLastCalledWith('sync', expect.objectContaining({platform: 'mercadolibre', catalog: 'current', mode: 'hot'}))

  // Ozon 已有商品时同样支持一键快刷。
  vi.mocked(onlineAction).mockResolvedValueOnce({...mlHotJob, id: 'sync-hot-ozon', platform: 'ozon'})
  await click('Ozon')
  await wrapper!.get('[data-testid="hot-refresh-btn"]').trigger('click'); await flushPromises()
  expect(onlineAction).toHaveBeenLastCalledWith('sync', expect.objectContaining({platform: 'ozon', catalog: 'current', mode: 'hot'}))

  // Yandex 已有商品时支持一键快刷并展示进度。
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), items: [row], summary: {total: 442, active: 360, paused: 0, attention: 82}})
  vi.mocked(onlineAction).mockResolvedValueOnce(hotJob)
  await click('Yandex Market')
  const hotBtn = wrapper!.get('[data-testid="hot-refresh-btn"]')
  vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(), items: [row], summary: {total: 442, active: 360, paused: 0, attention: 82}, latest_sync: hotJob, jobs: [hotJob]})
  await hotBtn.trigger('click'); await flushPromises()
  expect(onlineAction).toHaveBeenLastCalledWith('sync', expect.objectContaining({platform: 'yandex', catalog: 'current', mode: 'hot'}))
  const bar = wrapper!.get('[data-testid="online-sync-bar"]')
  expect(bar.text()).toContain('价格与库存快刷')
  expect(bar.text()).toContain('正在快速刷新价格、库存与销售状态，已处理 200 / 442 件')
})

it('不混入发布动态卡片，普通/归档收归表格筛选栏并支持多维组合筛选、概览快捷筛选与一键清空', async () => {
  const ingestJob: OnlineJob = {
    id: 'ingest-1', operation: 'sync', platform: 'yandex', status: 'running',
    created_at: '', updated_at: '2026-11-22T12:00:00Z', target_id: '*',
    request: {catalog: 'current', source: 'publish', ingest_count: 2},
    result: {phase: 'basic', discovered: 2, completed: 0, failed: 0},
  }
  vi.mocked(fetchOnlineProducts).mockResolvedValue({
    ...response(),
    items: [yandexItem()],
    warehouses: warehouseOptions,
    statuses: ['PUBLISHED', 'NO_STOCKS'],
    summary: {total: 445, active: 361, paused: 0, attention: 84},
    jobs: [ingestJob],
  })
  render(); await flushPromises(); await click('Yandex Market')

  // 不在在线页塞入发布状态列表，仅保留正在执行的定向入库进度条。
  expect(wrapper!.find('[data-testid="online-publish-items"]').exists()).toBe(false)
  expect(wrapper!.get('[data-testid="online-ingest-bar"]').text()).toContain('新发商品入库（2 件）执行中')
  // 顶栏不再放普通/归档标签，全部筛选维度集中在表格上方筛选区。
  expect(wrapper!.findAll('[role="tablist"]')).toHaveLength(1)
  const filterRow = wrapper!.get('[data-testid="online-filter-row"]')
  expect(filterRow.find('[data-testid="filter-catalog"]').exists()).toBe(true)
  expect(filterRow.find('[data-testid="filter-stock"]').exists()).toBe(true)
  expect(filterRow.find('[data-testid="filter-issue"]').exists()).toBe(true)
  expect(filterRow.find('[data-testid="filter-price"]').exists()).toBe(true)
  expect(filterRow.find('[data-testid="filter-kind"]').exists()).toBe(true)

  // 组合筛选：库存 + 反馈 + 价格 + 形态 + 目录。
  await wrapper!.get('[data-testid="filter-stock"]').setValue('out_of_stock'); await flushPromises()
  await wrapper!.get('[data-testid="filter-issue"]').setValue('error'); await flushPromises()
  await wrapper!.get('[data-testid="filter-price"]').setValue('missing'); await flushPromises()
  await wrapper!.get('[data-testid="filter-kind"]').setValue('group'); await flushPromises()
  expect(fetchOnlineProducts).toHaveBeenLastCalledWith(expect.objectContaining({
    platform: 'yandex',
    catalog: 'current',
    stock_state: 'out_of_stock',
    issue_state: 'error',
    price_state: 'missing',
    group_kind: 'group',
  }))

  // 清空筛选一次恢复全部默认条件。
  expect(wrapper!.get('[data-testid="reset-filters"]').text()).toContain('4')
  await wrapper!.get('[data-testid="reset-filters"]').trigger('click'); await flushPromises()
  const resetCall = vi.mocked(fetchOnlineProducts).mock.calls.at(-1)![0]
  expect(resetCall).toMatchObject({platform: 'yandex', catalog: 'current', status: ''})
  expect(resetCall).not.toHaveProperty('stock_state')
  expect(resetCall).not.toHaveProperty('issue_state')
  expect(resetCall).not.toHaveProperty('price_state')
  expect(resetCall).not.toHaveProperty('group_kind')

  // 点击顶部“需关注”指标快捷切到 issue_state=attention。
  await wrapper!.get('[data-testid="summary-attention"]').trigger('click'); await flushPromises()
  expect(fetchOnlineProducts).toHaveBeenLastCalledWith(expect.objectContaining({issue_state: 'attention'}))
})
