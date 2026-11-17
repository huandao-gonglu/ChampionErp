import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CollectBatchManager from '../CollectBatchManager.vue'
import type { CollectionLink } from '@/api/workflow/collectionLinks'

const rows: CollectionLink[] = [
  { id: 'a', revision: 4, url: 'https://example.com/1', title: '扳手', mode: 'browser', status: 'failed', platform: 'generic', updatedAt: '' },
  { id: 'b', revision: 1, url: 'https://example.com/2', title: '积木', mode: 'browser', status: 'pending', platform: 'generic', updatedAt: '' },
]

it('按名称或 URL 搜索，操作只作用于当前筛选范围', async () => {
  const wrapper = mount(CollectBatchManager, { props: { rows, ready: true, busy: false, selectedId: 'b' } })
  await wrapper.get('input').setValue('扳手')
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.emitted('select')?.at(-1)).toEqual(['a'])
  const button = (s: string) => wrapper.findAll('button').find(b => b.text().includes(s))!
  await button('重试失败').trigger('click')
  expect(wrapper.emitted('run')?.at(-1)).toEqual([[rows[0]]])
  expect(button('开始待采集').attributes('disabled')).toBeDefined()
  await wrapper.get('input').setValue('example.com/2')
  await button('开始待采集').trigger('click')
  expect(wrapper.emitted('run')?.at(-1)).toEqual([[rows[1]]])
  await wrapper.setProps({ busy: true })
  expect(button('开始待采集').attributes('disabled')).toBeDefined()
})
