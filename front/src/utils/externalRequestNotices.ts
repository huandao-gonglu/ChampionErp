import { ref } from 'vue'

export interface RequestNoticeItem {
  platform: string
  message: string
  code?: string
}
export interface RequestNoticeGroup {
  id: string
  source: string
  independentPlatforms: boolean
  items: RequestNoticeItem[]
}
interface Observation {
  until: number
  since: number
  group: Omit<RequestNoticeGroup, 'items'>
  reported: Set<string>
}

export const externalRequestNotices = ref<RequestNoticeGroup[]>([])
export const externalObservationRevision = ref(0)
const observed = new Map<string, Observation>()

const platformNames: Record<string, string> = {
  yandex: 'Yandex', ozon: 'Ozon', mercadolibre: 'Mercado Libre',
  'image_hosting:s3': '图片存储', 'image_hosting:public': '图片公开访问', crossborderbus: '跨境巴士',
  'ai:deepseek': 'AI · DeepSeek', 'ai:openai': 'AI · OpenAI',
}
export const requestPlatformName = (platform: string) => platformNames[platform] || platform

function operationSource(url: string) {
  const path = url.split('?')[0] || ''
  if (path === '/api/orders/sync') return '订单同步'
  if (path === '/api/orders/retry') return '订单任务重试'
  if (path.startsWith('/api/orders/')) return '订单操作'
  if (path.startsWith('/api/online')) return '在线商品操作'
  if (path.includes('research')) return '商品调研'
  if (path.includes('publish')) return '商品发布'
  if (path.includes('test-store-auth')) return '店铺授权测试'
  if (path.includes('test-ai')) return 'AI 连接测试'
  if (path.includes('generate-copy')) return '文案生成'
  return '本次操作'
}

export function observeExternalOperations(headers: Record<string, unknown>, method = '', url = '') {
  if (method.toLowerCase() !== 'post') return
  const ids = String(headers['x-external-operation-ids'] || '').split(',').filter(Boolean)
  const groupId = ids[0]
  if (!groupId) return
  // 同步请求被拦截时也查询其审计；已提交的后台任务继续关联同一次用户操作。
  const tracked = headers['x-external-request-blocked'] === '1' ? ids : ids.slice(1)
  for (const id of tracked) {
    observed.set(id, {
      until: Date.now() + 60 * 60_000,
      since: Number(headers['x-external-operation-since'] || 0),
      group: { id: groupId, source: operationSource(url), independentPlatforms: ['/api/orders/sync', '/api/orders/retry'].includes(url.split('?')[0] || '') },
      reported: new Set(),
    })
  }
  while (observed.size > 50) observed.delete(observed.keys().next().value!)
  if (tracked.length) externalObservationRevision.value++
}

export function observedExternalOperations() {
  for (const [id, value] of observed) if (value.until <= Date.now()) observed.delete(id)
  return [...observed.keys()]
}

export function acceptExternalNotices(notices: (RequestNoticeItem & { operation_id: string; created_at?: number })[]) {
  for (const notice of notices) {
    const observation = observed.get(notice.operation_id)
    if (!observation || observation.until <= Date.now() || (notice.created_at || 0) < observation.since) continue
    const key = JSON.stringify([notice.platform, notice.code || '', notice.message])
    if (observation.reported.has(key)) continue
    observation.reported.add(key)
    let group = externalRequestNotices.value.find(item => item.id === observation.group.id)
    if (!group) {
      group = { ...observation.group, items: [] }
      externalRequestNotices.value.push(group)
    }
    if (!group.items.some(item => item.platform === notice.platform && item.code === notice.code && item.message === notice.message)) {
      group.items.push({ platform: notice.platform, message: notice.message, code: notice.code })
    }
  }
}

export function dismissExternalNotice() {
  externalRequestNotices.value.shift()
}
