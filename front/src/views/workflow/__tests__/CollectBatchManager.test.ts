import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CollectBatchManager from '../CollectBatchManager.vue'
import type { CollectionLink } from '@/api/workflow/collectionLinks'

const rows: CollectionLink[] = [
  { id: 'a', revision: 4, url: 'https://example.com/1', title: '扳手', mode: 'browser', status: 'failed', platform: 'generic', updatedAt: '' },
  { id: 'b', revision: 1, url: 'https://example.com/2', title: '积木', mode: 'browser', status: 'pending', platform: 'generic', updatedAt: '' },
]
const listProps = {
  total: rows.length, counts: { failed: 1, pending: 1 }, selectedId: 'b', checkedIds: new Set<string>(),
  search: '', status: '' as const, sort: 'none' as const, ready: true, busy: false,
}

it('商品列表仅显示名称和 URL，即使采集结果含有主图', () => {
  const row = { ...rows[0], image: 'https://example.com/product.jpg' }
  const wrapper = mount(CollectBatchManager, { props: { ...listProps, rows: [row], selectedId: row.id } })
  const productCell = wrapper.get('[data-collect-row]')
  expect(productCell.text()).toContain(row.title)
  expect(productCell.text()).toContain(row.url)
  expect(productCell.find('img').exists()).toBe(false)
})

it('列表将搜索、排序和勾选交给页面，逐条采集只提交当前行', async () => {
  const wrapper = mount(CollectBatchManager, { props: { ...listProps, rows } })
  await wrapper.get('input').setValue('扳手')
  expect(wrapper.emitted('update:search')).toEqual([['扳手']])
  await wrapper.get('[aria-label="按商品名称或 URL 排序"]').trigger('click')
  expect(wrapper.emitted('update:sort')).toEqual([['ascending']])
  await wrapper.get('input[aria-label="选择 扳手"]').setValue(true)
  expect(wrapper.emitted('check')).toEqual([['a', true]])
  await wrapper.get('input[aria-label="全选当前筛选结果"]').setValue(true)
  expect(wrapper.emitted('checkAll')).toEqual([[true]])
  const rowAction = wrapper.findAll('tbody button').find(button => button.text() === '采集')!
  await rowAction.trigger('click')
  expect(wrapper.emitted('run')).toEqual([[rows[1]]])
  await wrapper.setProps({ busy: true })
  expect(rowAction.attributes('disabled')).toBeDefined()
})
