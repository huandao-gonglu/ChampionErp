import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import ExternalRequestNotice from '../ExternalRequestNotice.vue'
import { externalRequestNotices, observeExternalOperations } from '@/utils/externalRequestNotices'
import { apiClient } from '@/api/client'
vi.mock('@/api/client', () => ({ apiClient: { get: vi.fn() } }))
enableAutoUnmount(afterEach)
beforeEach(() => {
  externalRequestNotices.value = []
  vi.mocked(apiClient.get).mockResolvedValue({ data: { notices: [] } })
})
const render = () => mount(ExternalRequestNotice, { global: { stubs: {
  RouterLink: { props: ['to'], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' },
} } })
it('按来源和平台展示原因，链接定位全部受阻平台，不宣称其他平台成功', async () => {
  externalRequestNotices.value = [{ id: 'operation', source: '订单同步', independentPlatforms: true, items: [
    { platform: 'ozon', message: '账号 API 已停用' }, { platform: 'mercadolibre', message: '凭据失效' },
  ] }]
  const wrapper = render()
  expect(wrapper.get('[role="alert"]').text()).toContain('订单同步：Ozon、Mercado Libre 请求受阻')
  expect(wrapper.findAll('li')).toHaveLength(2)
  expect(wrapper.text()).toContain('其他平台继续独立处理')
  expect(wrapper.text()).not.toContain('同步成功')
  const target = JSON.parse(wrapper.get('a').attributes('data-to')!)
  expect(target.query).toEqual({ tab: 'auth', auth_section: 'interruptions', request_platform: ['ozon', 'mercadolibre'] })
  await wrapper.get('button').trigger('click')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})
it('新的手动操作唤醒查询，同步拦截无需等待轮询才显示来源', async () => {
  const wrapper = render()
  await flushPromises()
  vi.mocked(apiClient.get).mockResolvedValue({ data: { notices: [{ operation_id: 'blocked-http', platform: 'ozon', message: '平台停用', created_at: 101 }] } })
  observeExternalOperations({ 'x-external-request-blocked': '1', 'x-external-operation-ids': 'blocked-http', 'x-external-operation-since': '100' }, 'post', '/api/test-store-auth')
  await flushPromises()
  expect(wrapper.text()).toContain('店铺授权测试：Ozon 请求受阻')
  expect(wrapper.text()).not.toContain('其他平台继续独立处理')
})
