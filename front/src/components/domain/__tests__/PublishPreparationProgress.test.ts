import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PublishPreparationProgress from '../PublishPreparationProgress.vue'

describe('发布素材准备进度', () => {
  it('图片 100% 后仍展示最终校验，只有最终回执成功才显示就绪', async () => {
    const wrapper = mount(PublishPreparationProgress, { props: { progress: { stage: 'validating_source' } } })
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    await wrapper.setProps({ progress: { stage: 'uploading', completed: 139, total: 139 } })
    expect(wrapper.text()).toContain('139 / 139 张（100%）')
    expect(wrapper.text()).not.toContain('预览已就绪')
    await wrapper.setProps({ progress: { stage: 'validating_final', completed: 139, total: 139 } })
    expect(wrapper.text()).toContain('正在执行最终发布校验')
    expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('139')
    await wrapper.setProps({ progress: { stage: 'completed', completed: 139, total: 139 } })
    expect(wrapper.text()).toContain('素材与发布预览已就绪')
  })
})
