import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CollectBatchManager from '../CollectBatchManager.vue'
import { appendCollectUrls, createCollectBatchRow, restoreCollectQueue } from '@/utils/collectQueue'
import type { CollectBatchRow } from '@/types/workflow'

function setup(rows: CollectBatchRow[] = []) {
  const wrapper = mount(CollectBatchManager, { props: { rows, loading: false } })
  async function syncRows() {
    const updated = wrapper.emitted('update')?.at(-1)?.[0] as CollectBatchRow[]
    await wrapper.setProps({ rows: updated })
    return updated
  }
  const button = (name: string) => wrapper.findAll('button').find((item) => item.text() === name)!
  return { wrapper, syncRows, button }
}

describe('批量采集列表', () => {
  it('支持添加、自动去重、编辑与删除 URL', async () => {
    const { wrapper, button, syncRows } = setup()
    await wrapper.get('textarea').setValue('https://example.com/a\nhttps://example.com/b\nhttps://example.com/a#title')
    await button('添加到列表').trigger('click')
    const rows = await syncRows()
    expect(rows).toHaveLength(2)
    expect(rows.map((row) => row.status)).toEqual(['pending', 'pending'])
    expect(wrapper.text()).toContain('跳过 1 条重复链接')
    await button('编辑').trigger('click')
    await wrapper.get('input[aria-label="编辑 URL"]').setValue('https://example.com/edited')
    await wrapper.get('form').trigger('submit')
    const edited = await syncRows()
    expect(edited[0]).toMatchObject({ id: rows[0].id, url: 'https://example.com/edited', status: 'pending' })
    await button('删除').trigger('click')
    expect(await syncRows()).toHaveLength(1)
  })

  it('无效链接不会部分入列，重复编辑保留原数据', async () => {
    const rows = appendCollectUrls([], 'https://example.com/a https://example.com/b').rows
    const { wrapper, button } = setup(rows)
    await wrapper.get('textarea').setValue('https://example.com/c\njavascript:alert(1)')
    await button('添加到列表').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('第 2 个链接无效')
    expect(wrapper.emitted('update')).toBeUndefined()
    await button('编辑').trigger('click')
    await wrapper.get('input').setValue(rows[1].url)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('[role="alert"]').text()).toContain('已在列表中')
    expect(wrapper.emitted('update')).toBeUndefined()
  })

  it('显示五态、失败原因，运行中禁止编辑删除及重复开始', async () => {
    const rows = ['pending', 'running', 'success', 'failed', 'waiting_verification'].map((status, index) => ({
      ...createCollectBatchRow(`https://example.com/${index}`), status,
      error: status === 'failed' ? '需要登录商品页面' : '',
    })) as CollectBatchRow[]
    const { wrapper, button } = setup(rows)
    await wrapper.setProps({ loading: true })
    for (const label of ['未开始', '正在采集', '采集完成', '采集失败', '需要登录商品页面']) expect(wrapper.text()).toContain(label)
    expect(button('编辑').attributes('disabled')).toBeDefined()
    expect(button('删除').attributes('disabled')).toBeDefined()
    expect(button('重试失败项').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ loading: false })
    await button('重试失败项').trigger('click')
    expect(wrapper.emitted('collect')?.[0]).toEqual([[rows[3].id]])
    await button('采集失败 1').trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  })

  it('刷新恢复链接与结果，并标记未确认任务，容忍损坏的本地存储', () => {
    const rows = appendCollectUrls([], 'https://example.com/a https://example.com/b').rows
    rows[0].status = 'running'
    rows[1].status = 'success'
    const restored = restoreCollectQueue(JSON.stringify(rows))
    expect(restored.map((row) => row.status)).toEqual(['failed', 'success'])
    expect(restored[0].error).toContain('未确认采集结果')
    expect(restoreCollectQueue('invalid')).toEqual([])
  })
  it('刷新后保留等待验证及原标签，允许继续采集', async () => {
    const row = createCollectBatchRow('https://detail.1688.com/offer/123.html')
    row.status = 'waiting_verification'
    row.verification = { browserTabId: 'original', sourceUrl: row.url, platform: '1688' }
    const restored = restoreCollectQueue(JSON.stringify([row]))
    expect(restored[0]).toMatchObject({ status: 'waiting_verification', verification: row.verification, error: '' })
    const { button } = setup(restored)
    expect(button('开始采集（1）').attributes('disabled')).toBeUndefined()
  })

})
