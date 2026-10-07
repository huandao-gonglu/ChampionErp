import { afterEach, expect, it, vi } from 'vitest'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import ExternalRequestNotice from '../ExternalRequestNotice.vue'
import { externalRequestNotice } from '@/utils/externalRequestNotices'
vi.mock('@/api/client', () => ({ apiClient: { get: vi.fn() } }))
enableAutoUnmount(afterEach)
it('暂停提示提供直达授权中断页的链接并可关闭', async () => {
  externalRequestNotice.value = '本次操作因冷却暂停'
  const wrapper = mount(ExternalRequestNotice, { global: { stubs: {
    RouterLink: { props: ['to'], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' },
  } } })
  expect(wrapper.get('[role="alert"]').text()).toContain('冷却暂停')
  expect(wrapper.get('a').attributes('data-to')).toContain('interruptions')
  expect(wrapper.get('a').attributes('data-to')).toContain('auth')
  await wrapper.get('button').trigger('click')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})
