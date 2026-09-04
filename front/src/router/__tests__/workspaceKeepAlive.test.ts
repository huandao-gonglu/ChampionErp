/* eslint-disable vue/one-component-per-file -- 测试内集中定义两个最小路由夹具 */
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import App from '@/App.vue'

const WorkflowHarness = defineComponent({
  name: 'WorkflowHarness',
  setup() {
    const editorOpen = ref(false)
    return () => h('main', [
      h('button', {
        'data-testid': 'open-draft-editor',
        onClick: () => { editorOpen.value = true },
      }, '打开草稿编辑'),
      editorOpen.value
        ? h('section', { 'data-testid': 'draft-editor' }, '草稿编辑页面')
        : null,
    ])
  },
})

const AiWorkHarness = defineComponent({
  name: 'AiWorkHarness',
  setup: () => () => h('main', { 'data-testid': 'ai-work-page' }, 'AI Work'),
})

describe('工作台路由状态保活', () => {
  it('进入 AI Work 再返回时保留已打开的草稿编辑页面', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          name: 'WorkflowHome',
          component: WorkflowHarness,
          meta: { keepAlive: true },
        },
        { path: '/aiWork', name: 'AiWork', component: AiWorkHarness },
      ],
    })
    await router.push('/?tab=drafts')
    await router.isReady()

    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          AiWorkFloatingButton: true,
          NavigationProgress: true,
          Toast: true,
        },
      },
    })

    await wrapper.get('[data-testid="open-draft-editor"]').trigger('click')
    expect(wrapper.get('[data-testid="draft-editor"]').text()).toBe('草稿编辑页面')

    await router.push('/aiWork?workspace_tab=drafts')
    expect(wrapper.find('[data-testid="draft-editor"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ai-work-page"]').text()).toBe('AI Work')

    await router.push('/?tab=drafts')
    expect(wrapper.get('[data-testid="draft-editor"]').text()).toBe('草稿编辑页面')
  })
})
