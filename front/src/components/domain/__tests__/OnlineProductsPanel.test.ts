import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { apiClient } from '@/api/client'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import OnlineProductsPanel from '../OnlineProductsPanel.vue'
import OzonExportDialog from '../OzonExportDialog.vue'
import OnlineOperationRecovery from '../OnlineOperationRecovery.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchOnlineDetail, fetchOnlineJob, fetchOnlineProducts, fetchOnlineSourceImages, onlineAction, previewOnlineBatch, submitOnlineBatch, refreshOnlineStatus, type OnlineJob, type OnlineListing, type OnlinePage } from '@/api/onlineProducts'

vi.mock('@/api/onlineProducts', () => ({ fetchOnlineDetail: vi.fn(), fetchOnlineJob: vi.fn(), fetchOnlineProducts: vi.fn(), fetchOnlineSourceImages: vi.fn(), onlineAction: vi.fn(), previewOnlineBatch:vi.fn(), submitOnlineBatch:vi.fn(), refreshOnlineStatus: vi.fn() }))
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
beforeEach(() => {setActivePinia(createPinia());vi.useFakeTimers(); vi.resetAllMocks();vi.mocked(fetchOnlineProducts).mockResolvedValue(response());vi.mocked(fetchOnlineDetail).mockResolvedValue(item);vi.mocked(previewOnlineBatch).mockResolvedValue({items:[{listing_id:item.id,version:item.version,seller_sku:item.seller_sku,title:item.title,group_id:'single-1'}],errors:[],product_count:1,scope:'商家账号全部店铺'})})
afterEach(() => {wrapper?.unmount();wrapper=undefined;document.body.innerHTML='';apiClient.defaults.adapter=defaultAdapter;vi.useRealTimers()})
function render(){wrapper=mount(OnlineProductsPanel,{attachTo:document.body,global:{stubs:{Teleport:true,OzonExportDialog:true}}});return wrapper}
async function click(text:string){const button=wrapper!.findAll('button').find(b=>b.text()===text);expect(button, text).toBeDefined();await button!.trigger('click');await flushPromises()}

describe('在线商品页面',()=>{
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
    await wrapper!.get('[aria-label="关闭操作详情"]').trigger('click'); await click('返回在线商品')
    expect(wrapper!.find('[data-testid="online-listing"]').exists()).toBe(false)
    await click('归档商品')
    expect(fetchOnlineProducts).toHaveBeenLastCalledWith(expect.objectContaining({catalog:'archived'}))
    expect(useAiPageContextStore().current).toMatchObject({catalog:'archived'})
    expect(useAiPageContextStore().current?.listing_id).toBeUndefined()
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain('已归档')
    await click('管理 →')
    expect(wrapper!.findAll('button').find(button => button.text()==='停售商品')!.attributes('disabled')).toBeDefined()
    await click('恢复归档'); await click('预览变更'); await click('确认提交')
    expect(onlineAction).toHaveBeenLastCalledWith('change',expect.objectContaining({operation:'archive',scope_id:'business',changes:{archived:false}}))
    await wrapper!.get('[aria-label="关闭操作详情"]').trigger('click'); await click('返回在线商品')
    expect(wrapper!.find('[data-testid="online-listing"]').exists()).toBe(false)
    await click('普通商品')
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
    if ('archived' in changes && !changes.archived) await click('归档商品')
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
    await wrapper!.get('[aria-label="关闭恢复所选商品销售"]').trigger('click'); await click('归档商品')
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
  it('明确受理与实际生效分开展示，不提示必须核对后才能继续修改', async () => {
    const job: OnlineJob = {id:'accepted',operation:'sale_state',platform:'yandex',status:'submitted',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
    render(); await flushPromises(); await click('操作记录'); await click('查看详情')
    const detail = wrapper!.get('[data-testid="online-job-detail"]')
    expect(detail.text()).toContain('停售 / 恢复 · 平台已受理')
    expect(detail.text()).toContain('可继续修改商品')
    expect(detail.text()).not.toContain('已生效')
    expect(wrapper!.find('[data-testid="review-unconfirmed"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(60000)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it.each(['NO_STOCKS', 'PUBLISHED'])('库存确认后自动查询销售状态并展示平台实际值 %s', async rawStatus => {
    const job: OnlineJob = {id:'stock-confirm',operation:'stock',platform:'yandex',status:'submitted',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}}
    const confirmed = {...job,status:'confirmed'}
    const fresh = {...item,raw_status:rawStatus,sale_state:rawStatus === 'PUBLISHED' ? 'active' : 'unknown',stocks:[{id:'warehouse',label:'库存仓',quantity:0,writable:true,reason:''}]}
    vi.mocked(fetchOnlineProducts).mockResolvedValueOnce({...response(),jobs:[job]}).mockResolvedValue({...response(),items:[fresh],jobs:[confirmed]})
    vi.mocked(onlineAction).mockResolvedValueOnce(confirmed)
    vi.mocked(refreshOnlineStatus).mockResolvedValueOnce(fresh)
    render(); await flushPromises(); await click('操作记录'); await click('查看详情'); await click('查询平台结果')
    expect(refreshOnlineStatus).toHaveBeenCalledTimes(1)
    expect(refreshOnlineStatus).toHaveBeenCalledWith(item.id)
    expect(wrapper!.get('[data-testid="online-job-detail"]').text()).toContain('修改库存 · 已生效')
    await wrapper!.get('[aria-label="关闭操作详情"]').trigger('click'); await click('返回在线商品')
    expect(wrapper!.get('[data-testid="online-listing"]').text()).toContain(rawStatus === 'NO_STOCKS' ? '缺货' : '在售')
    expect(onlineAction).toHaveBeenCalledTimes(1)
    expect(onlineAction).toHaveBeenCalledWith('reconcile', {job_id:job.id})
  })
  it('核对弹窗确认库存成功后同样刷新状态；状态查询失败不改变库存成功结果', async () => {
    const job: OnlineJob = {id:'stock-confirm',operation:'stock',platform:'mercadolibre',status:'outcome_unknown',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}}
    const confirmed = {...job,status:'confirmed'}
    vi.mocked(fetchOnlineProducts).mockResolvedValueOnce({...response(),jobs:[job]}).mockResolvedValue({...response(),jobs:[confirmed]})
    vi.mocked(refreshOnlineStatus).mockRejectedValueOnce(new Error('状态接口超时'))
    render(); await flushPromises(); await click('操作记录'); await click('查看详情')
    wrapper!.findComponent(OnlineOperationRecovery).vm.$emit('updated', confirmed)
    await flushPromises()
    expect(refreshOnlineStatus).toHaveBeenCalledTimes(1)
    expect(refreshOnlineStatus).toHaveBeenCalledWith(item.id)
    const dialog = wrapper!.get('[data-testid="online-job-detail"]')
    expect(dialog.text()).toContain('修改库存 · 已生效')
    expect(dialog.get('[role="alert"]').text()).toBe('库存已生效，但销售状态刷新失败：状态接口超时')
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it.each(['submitted','waiting_confirmation','outcome_unknown'])('库存仍为 %s 时不额外查询销售状态', async status => {
    const job: OnlineJob = {id:'stock-confirm',operation:'stock',platform:'mercadolibre',status:'submitted',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[{...job,status}]})
    vi.mocked(onlineAction).mockResolvedValueOnce({...job,status})
    render(); await flushPromises(); await click('操作记录'); await click('查看详情'); await click('查询平台结果')
    expect(refreshOnlineStatus).not.toHaveBeenCalled()
  })
  it('真实 HTTP 错误经过客户端后仍可直达列表之外的对应操作，不重发修改', async () => {
    const blocking: OnlineJob = {id:'blocking-job',operation:'stock',platform:'mercadolibre',status:'submitted',created_at:'',updated_at:'',target_id:item.id,request:{},result:{before:{seller_sku:'BLOCKED-SKU'}}}
    const actual = await vi.importActual<typeof import('@/api/onlineProducts')>('@/api/onlineProducts')
    vi.mocked(onlineAction).mockImplementationOnce(actual.onlineAction)
    vi.mocked(fetchOnlineJob).mockImplementationOnce(actual.fetchOnlineJob)
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      if (config.method === 'post' && config.url === '/api/online-products/change') {
        throw new AxiosError('Request failed with status code 409', 'ERR_BAD_REQUEST', config, undefined, {
          config, status:409, statusText:'Conflict', headers:{},
          data:{ok:false,error:'该商品已有未完成的修改',error_code:'ONLINE_CONFLICT',conflict_job_id:blocking.id},
        })
      }
      expect(config.method).toBe('get')
      expect(config.url).toBe('/api/online-products')
      expect(config.params).toEqual({job_id:blocking.id})
      return {config,status:200,statusText:'OK',headers:{},data:{ok:true,job:blocking}}
    })
    apiClient.defaults.adapter = adapter
    render(); await flushPromises(); await click('管理 →'); await click('调整价格')
    await wrapper!.get('input[inputmode="decimal"]').setValue('18.25')
    await click('预览变更'); await click('确认提交')
    await wrapper!.get('[data-testid="online-conflict-operation"]').trigger('click'); await flushPromises()
    expect(fetchOnlineJob).toHaveBeenCalledWith('blocking-job')
    const dialog = wrapper!.get<HTMLDialogElement>('[data-testid="online-job-dialog"]')
    expect(dialog.element.open).toBe(true)
    expect(dialog.text()).toContain('BLOCKED-SKU')
    expect(dialog.text()).toContain('查询平台结果')
    expect(wrapper!.find('[aria-label="商品详情"]').exists()).toBe(false)
    expect(wrapper!.find('[aria-label="调整价格"]').exists()).toBe(false)
    expect(onlineAction).toHaveBeenCalledTimes(1)
    expect(adapter).toHaveBeenCalledTimes(2)
  })
  it('对应操作读取失败时保留变更弹窗与重试入口，普通错误不显示跳转按钮', async () => {
    vi.mocked(onlineAction).mockRejectedValueOnce(Object.assign(new Error('已有未完成修改'), {
      raw:{error_code:'ONLINE_CONFLICT',conflict_job_id:'blocking-job'},
    }))
    vi.mocked(fetchOnlineJob).mockRejectedValueOnce(new Error('操作记录读取失败'))
    render(); await flushPromises(); await click('管理 →'); await click('调整价格')
    await click('预览变更'); await click('确认提交'); await click('查看对应操作')
    expect(wrapper!.get('[aria-label="调整价格"]').text()).toContain('操作记录读取失败')
    expect(wrapper!.find('[data-testid="online-conflict-operation"]').exists()).toBe(true)
    vi.mocked(onlineAction).mockRejectedValueOnce(new Error('网络错误'))
    await click('确认提交')
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
  it('历史延迟确认标记不再启动自动查询', async () => {
    const job: OnlineJob = {id:'submitted',operation:'price',platform:'mercadolibre',status:'submitted',target_id:item.id,created_at:'',updated_at:'',request:{},result:{automatic_confirmation_pending:true,next_confirmation_at:Date.now()/1000+120}}
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
    await click('操作记录')
    expect(context.current?.group_id).toBeUndefined()
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
  })
  it('长记录列表点击详情立即打开对应抽屉，查询错误显示在抽屉内', async () => {
    const jobs: OnlineJob[] = Array.from({length:100}, (_, index) => ({
      id:`job-${index}`,operation:'stock',platform:'mercadolibre',status:'submitted',
      created_at:'',updated_at:'',target_id:`listing-${index}`,request:{},
      result:{before:{title:`商品 ${index}`,seller_sku:`SKU-${index}`}},
    }))
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs})
    render(); await flushPromises(); await click('操作记录')
    const details = wrapper!.findAll('button').filter(button => button.text() === '查看详情')
    await details[0].trigger('click'); await flushPromises()
    const dialog = () => wrapper!.get('[data-testid="online-job-dialog"]')
    expect((dialog().element as HTMLDialogElement).open).toBe(true)
    expect(dialog().text()).toContain('SKU-0')
    expect(dialog().findAll('button').some(button => button.text() === '查询平台结果')).toBe(true)
    expect(onlineAction).not.toHaveBeenCalled()
    await dialog().get('[aria-label="关闭操作详情"]').trigger('click'); await flushPromises()
    expect((dialog().element as HTMLDialogElement).open).toBe(false)

    await details[99].trigger('click'); await flushPromises()
    expect((dialog().element as HTMLDialogElement).open).toBe(true)
    expect(dialog().text()).toContain('SKU-99')
    vi.mocked(onlineAction).mockRejectedValueOnce(new Error('请稍后再查询'))
    await click('查询平台结果')
    expect(onlineAction).toHaveBeenCalledWith('reconcile', {job_id:'job-99'})
    expect(dialog().get('[role="alert"]').text()).toBe('请稍后再查询')
  })
  it('操作详情内部按下、遮罩松开不关闭，完整遮罩点击才关闭', async () => {
    const job: OnlineJob = {id:'submitted',operation:'stock',platform:'mercadolibre',status:'submitted',created_at:'',updated_at:'',target_id:item.id,request:{},result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[job]})
    render(); await flushPromises(); await click('操作记录'); await click('查看详情')
    const dialog = () => wrapper!.get('[data-testid="online-job-dialog"]')
    const backdrop = wrapper!.get('[data-testid="workspace-dialog-backdrop"]')
    const pointer = {isPrimary:true,button:0,pointerId:1}
    await dialog().get('h2').trigger('pointerdown', pointer)
    await backdrop.trigger('pointerup', pointer)
    await flushPromises()
    expect((dialog().element as HTMLDialogElement).open).toBe(true)
    await backdrop.trigger('pointerdown', pointer)
    await backdrop.trigger('pointerup', pointer)
    await flushPromises()
    expect((dialog().element as HTMLDialogElement).open).toBe(false)
  })
  it('旧失败记录指向最新修改，不继续重试旧记录', async () => {
    const old: OnlineJob = {id:'old',operation:'stock',platform:'yandex',status:'failed',created_at:'2026-10-13T10:12:00Z',updated_at:'',target_id:item.id,request:{},result:{error:'Yandex 接口请求超时'}}
    const latest: OnlineJob = {...old,id:'latest',status:'submitted',created_at:'2026-10-13T13:04:00Z',result:{}}
    vi.mocked(fetchOnlineProducts).mockResolvedValue({...response(),jobs:[latest,old]})
    render(); await flushPromises(); await click('操作记录')
    await wrapper!.findAll('button').filter(button => button.text() === '查看详情')[1].trigger('click')
    await flushPromises()
    expect(wrapper!.get('[data-testid="online-job-newer"]').text()).toContain('平台已受理')
    expect(wrapper!.get('[data-testid="online-job-detail"]').text()).not.toContain('仅重试失败项')
    await click('查看最新操作')
    const detail = wrapper!.get('[data-testid="online-job-detail"]')
    expect(detail.text()).toContain('查询平台结果')
    expect(detail.text()).not.toContain('Yandex 接口请求超时')
    expect(detail.find('[data-testid="online-job-newer"]').exists()).toBe(false)
    expect(onlineAction).not.toHaveBeenCalled()
  })
  it('结果未知提供核对与人工结束入口，不提供重试',async()=>{
    const page=response();page.jobs=[{id:'unknown',operation:'price',platform:'mercadolibre',status:'outcome_unknown',created_at:'',updated_at:'',target_id:'remote-1',request:{},result:{error:'平台可能已接受'}}]
    vi.mocked(fetchOnlineProducts).mockResolvedValue(page)
    render();await flushPromises();await click('操作记录');await click('查看详情')
    expect(wrapper!.text()).toContain('查询平台结果');expect(wrapper!.text()).not.toContain('仅重试失败项')
    expect(wrapper!.get('[data-testid="review-unconfirmed"]').text()).toBe('核对并结束操作')
    await click('核对并结束操作')
    expect(wrapper!.get('[data-testid="close-unconfirmed"]').attributes('disabled')).toBeDefined()
  })
  it('人工结束后保留未知结果与历史错误，不继续显示阻断或重试入口', async () => {
    const page=response();page.jobs=[{id:'closed',operation:'stock',platform:'yandex',status:'closed_unconfirmed',created_at:'',updated_at:'',target_id:'remote-1',request:{},result:{error:'原请求超时',resolution:{closed_at:'2026-10-13T10:00:00Z',acknowledged_unconfirmed:true}}}]
    vi.mocked(fetchOnlineProducts).mockResolvedValue(page)
    render();await flushPromises();await click('操作记录');await click('查看详情')
    expect(wrapper!.get('article').text()).toContain('已结束，结果未确认')
    expect(wrapper!.get('article details').text()).toContain('原请求超时')
    expect(wrapper!.get('article').find('[role="alert"]').exists()).toBe(false)
    expect(wrapper!.text()).not.toContain('仅重试失败项')
    expect(wrapper!.find('[data-testid="review-unconfirmed"]').exists()).toBe(false)
    expect(onlineAction).not.toHaveBeenCalled()
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
    if (catalog==='archived') await click('归档商品')
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
    await click('归档商品')
    expect(wrapper!.text()).toContain('最近同步 · 归档商品')
    await click('查看同步进度')
    expect(onlineAction).not.toHaveBeenCalled()
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
