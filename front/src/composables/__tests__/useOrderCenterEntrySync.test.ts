import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { useOrderCenterEntrySync } from '../useOrderCenterEntrySync'

beforeEach(() => setActivePinia(createPinia()))
async function setup(path = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push(path)
  await router.isReady()
  const store = useOrderNotificationsStore()
  const sync = vi.spyOn(store, 'command').mockResolvedValue()
  const wrapper = mount(defineComponent({ setup() { useOrderCenterEntrySync() }, template: '<div />' }), { global: { plugins: [router] } })
  return { router, sync, wrapper }
}
describe('订单中心的自动同步导航入口', () => {
  it('只在导航进入时检查后端冷却，不因焦点、详情切换或时间到达而同步', async () => {
    vi.useFakeTimers()
    const { router, sync, wrapper } = await setup()
    try {
      expect(sync).not.toHaveBeenCalled()
      await router.push('/?tab=orders'); await flushPromises()
      expect(sync).toHaveBeenCalledTimes(1)
      expect(sync).toHaveBeenCalledWith('sync', { platform: '', automatic: true })
      await router.push('/?tab=orders&order=123'); await flushPromises()
      window.dispatchEvent(new Event('focus'))
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(6 * 3600_000)
      expect(sync).toHaveBeenCalledTimes(1)
      await router.push('/?tab=library'); await router.push('/?tab=orders'); await flushPromises()
      expect(sync).toHaveBeenCalledTimes(2)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })
  it('已在订单页时恢复或重新挂载不触发自动同步', async () => {
    const { sync, wrapper } = await setup('/?tab=orders')
    await flushPromises()
    expect(sync).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
