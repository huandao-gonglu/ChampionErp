import { apiClient } from '@/api/client'

const delay = () => new Promise<void>(resolve => setTimeout(resolve, 1500))

export class CollectionResultUnconfirmed extends Error {
  constructor() {
    super('未能确认本次采集结果，请先查看采集箱。')
  }
}

function transportInterrupted(error: unknown) {
  const { status } = error as { status?: number }
  return !status || [408, 502, 503, 504].includes(status)
}

/** 采集只提交一次。短暂断线时仅重查同一份回执。 */
export async function submitCollection(path: string, body: Record<string, unknown>) {
  const receiptId = crypto.randomUUID()
  try {
    const response = await apiClient.post(path, { ...body, receipt_id: receiptId })
    // 已安装的同步服务也可能直接返回完整业务结果。
    if (response.data?.receipt_id !== receiptId) return response
  } catch (error) {
    if (!transportInterrupted(error) && (error as { code?: string }).code !== 'COLLECT_ALREADY_STARTED') throw error
  }

  let lastContact = Date.now()
  while (true) {
    try {
      const response = await apiClient.post('/api/collect-receipt', { receipt_id: receiptId }, { timeout: 10_000 })
      const data = response.data
      if (data?.receipt_id !== receiptId) throw new CollectionResultUnconfirmed()
      if (data.status === 'finished' && data.result) return { data: data.result }
      if (data.status !== 'running') throw new CollectionResultUnconfirmed()
      lastContact = Date.now()
    } catch (error) {
      const { status, code } = error as { status?: number; code?: string }
      if (error instanceof CollectionResultUnconfirmed || code === 'COLLECT_RESULT_UNCONFIRMED') throw new CollectionResultUnconfirmed()
      if (status === 401 || status === 403) throw new CollectionResultUnconfirmed()
      if (status !== 404 && !transportInterrupted(error)) throw error
      if (Date.now() - lastContact >= 30_000) throw new CollectionResultUnconfirmed()
    }
    await delay()
  }
}
