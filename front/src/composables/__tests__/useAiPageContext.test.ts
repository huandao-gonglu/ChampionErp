import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { useAiPageContext } from '../useAiPageContext'
import { useAiPageContextStore } from '@/stores/aiPageContext'

describe('页面背景生命周期', () => {
  it('订单详情切换时更新 ERP 订单身份，关闭后恢复列表且不保留旧订单', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAiPageContextStore()
    const orderId = ref('yandex:4:123')
    const opened = ref(true)
    const Detail = defineComponent({ setup() {
      useAiPageContext(() => ({ page: 'orders', order_id: orderId.value }), 10)
      return () => h('div')
    } })
    const Page = defineComponent({ setup() {
      useAiPageContext(() => ({ page: 'orders' }))
      return () => opened.value ? h(Detail) : h('div')
    } })
    const wrapper = mount(Page, { global: { plugins: [pinia] } })
    const sent = store.snapshot()
    orderId.value = 'yandex:4:456'
    expect(store.current?.order_id).toBe('yandex:4:456')
    expect(sent?.order_id).toBe('yandex:4:123')
    opened.value = false
    await nextTick()
    expect(store.snapshot()).toEqual({ page: 'orders' })
    wrapper.unmount()
  })

  it('关闭子编辑器后恢复页面背景，缓存页面停用时移除旧草稿位置', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAiPageContextStore()
    const editing = ref(true)
    const visible = ref(true)
    const draftId = ref('draft-a')
    const Child = defineComponent({
      setup() {
        useAiPageContext(() => ({ page: 'draft_editor', draft_id: draftId.value, section: 'category' }), 10)
        return () => h('div', '属性编辑')
      },
    })
    const Page = defineComponent({
      setup() {
        useAiPageContext(() => ({ page: 'drafts' }))
        return () => editing.value ? h(Child) : h('div', '草稿箱')
      },
    })
    const Root = defineComponent({ setup: () => () => h(KeepAlive, () => visible.value ? h(Page) : null) })
    const wrapper = mount(Root, { global: { plugins: [pinia] } })
    expect(store.current?.draft_id).toBe('draft-a')
    draftId.value = 'draft-b'
    expect(store.current?.draft_id).toBe('draft-b')
    editing.value = false
    await nextTick()
    expect(store.current).toEqual({ page: 'drafts' })
    visible.value = false
    await nextTick()
    expect(store.current).toBeNull()
    // 停用期间的数据更新不能重新发布隐藏页面。
    draftId.value = 'draft-c'
    editing.value = true
    await nextTick()
    expect(store.current).toBeNull()
    visible.value = true
    await nextTick()
    expect(store.current?.draft_id).toBe('draft-c')
    wrapper.unmount()
    expect(store.current).toBeNull()
  })
})
