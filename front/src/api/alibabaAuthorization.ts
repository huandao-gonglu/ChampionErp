import { apiClient } from '@/api/client'
import type { BackendAlibabaAuthorizationStatus } from '@/types/workflow.generated'

function authPath(crossborder: boolean) {
  return crossborder ? '/api/alibaba/crossborder/authorization' : '/api/alibaba/authorization'
}
export async function fetchAlibabaAuthorization(crossborder = false): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.get(authPath(crossborder))).data
}
export async function startAlibabaAuthorization(crossborder = false): Promise<{ ok: boolean; url: string }> {
  return (await apiClient.post(authPath(crossborder) + '/start', {})).data
}
export async function completeAlibabaAuthorization(callbackUrl: string, crossborder = false): Promise<BackendAlibabaAuthorizationStatus> {
  return (await apiClient.post(authPath(crossborder) + '/complete', { callback_url: callbackUrl })).data
}
export async function testAlibabaOrder(orderNumber: string): Promise<void> {
  await apiClient.post('/api/test-api-config', { kind: '1688_order', config: {}, test_value: orderNumber })
}

export async function testAlibabaCrossborderProduct(productId: string): Promise<{ sku_count: number; spec_id_count: number }> {
  return (await apiClient.post(authPath(true) + '/test-product', { product_id: productId })).data
}
