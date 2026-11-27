import { createPinia, setActivePinia } from 'pinia'
// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DraftBoxPanel from '@/components/domain/DraftBoxPanel.vue'
import { useAiPageContextStore } from '@/stores/aiPageContext'
import type { DraftIndexItem, MarketplaceOption } from '@/types/workflow'

const platformOptions: MarketplaceOption[] = [{
  key: 'mercadolibre',
  label: '美客多',
  sites: [
    { key: 'CBT', code: 'CBT', label: '全局', language: 'en-US' },
    { key: 'MLM', code: 'MLM', label: '墨西哥', language: 'es' },
    { key: 'MCO', code: 'MCO', label: '哥伦比亚', language: 'es' },
    { key: 'MLB', code: 'MLB', label: '巴西', language: 'pt-BR' },
  ],
}]

const storeConfig = {
  mercadolibre: {
    account_site_id: 'CBT',
    listing_model: 'traditional_global_items',
    marketplace_bindings: [
      { seller_id: 'seller-mx', site_id: 'MLM', logistic_type: 'remote', pricing_model: 'price' },
      { seller_id: 'seller-co', site_id: 'MCO', logistic_type: 'remote', pricing_model: 'price' },
      { seller_id: 'seller-br', site_id: 'MLB', logistic_type: 'remote', pricing_model: 'price' },
    ],
  },
}

function draft(draftId: string, status: string, title: string): DraftIndexItem {
  return {
    draftId,
    productId: `product-${draftId}`,
    sourceProductId: `source-${draftId}`,
    platform: 'mercadolibre',
    platforms: ['mercadolibre'],
    targetSites: [],
    site: 'MLM',
    language: 'es',
    status,
    title,
    productTitle: title,
    mainImage: '',
    sourcePlatform: '1688',
    sourceUrl: '',
    categoryId: '',
    categoryPath: '',
    publishStatus: '',
    createdAt: '',
    updatedAt: '',
    productFilePath: '',
    raw: {},
  }
}

describe('DraftBoxPanel', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('勾选、取消与删除同步到聊天背景，发送快照不受后续改选影响', async () => {
    localStorage.removeItem('ai-chat-read-background')
    const rows = [draft('draft-a', 'claimed', '草稿 A'), draft('draft-b', 'claimed', '草稿 B')]
    const wrapper = mount(DraftBoxPanel, {
      props: { drafts: rows, platformOptions: [], storeConfig: {}, loading: false },
    })
    const context = useAiPageContextStore()
    expect(context.snapshot()).toEqual({ page: 'drafts', selected_draft_ids: [] })
    await wrapper.get('[aria-label="全选当前草稿"]').setValue(true)
    const sent = context.snapshot()
    expect(sent?.selected_draft_ids).toEqual(['draft-a', 'draft-b'])
    await wrapper.findAll('[aria-label="勾选草稿"]')[0].setValue(false)
    expect(context.snapshot()?.selected_draft_ids).toEqual(['draft-b'])
    expect(sent?.selected_draft_ids).toEqual(['draft-a', 'draft-b'])
    await wrapper.setProps({ drafts: [rows[0]] })
    expect(context.snapshot()?.selected_draft_ids).toEqual([])
    wrapper.unmount()
    expect(context.snapshot()).toBeUndefined()
  })
  it('改写刊登标题后仍显示采集标题，缺少商品名称时显示商品编号', async () => {
    const row = draft('fan', 'claimed', '便携风扇')
    const wrapper = mount(DraftBoxPanel, {
      props: { drafts: [row], platformOptions: [], storeConfig: {}, loading: false },
    })
    const localized = { ...row, title: 'Ventilador portátil' }
    await wrapper.setProps({ drafts: [localized] })
    expect(wrapper.text()).toContain(row.productTitle)
    expect(wrapper.text()).not.toContain(localized.title)

    await wrapper.setProps({ drafts: [{ ...localized, productTitle: '' }] })
    expect(wrapper.text()).toContain(row.productId)
    expect(wrapper.text()).not.toContain(localized.title)
  })

  it('展示草稿创建时间', () => {
    const row = draft('created-time', 'claimed', '带创建时间的草稿')
    row.createdAt = '2026-09-01T12:34:56+08:00'
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [row],
        platformOptions: [],
        storeConfig: {},
        loading: false,
      },
    })

    expect(wrapper.text()).toContain('创建时间')
    const createdAt = wrapper.get('[data-testid="draft-created-at"]')
    expect(createdAt.attributes('title')).toBe(row.createdAt)
    expect(createdAt.text()).toContain('2026')
    expect(createdAt.text()).toContain('12:34:56')
  })

  it('点击复制草稿时请求创建副本，不操作剪贴板', async () => {
    const row = draft('draft-to-copy', 'claimed', '待复制草稿')
    const writeText = vi.fn()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [row],
        platformOptions: [],
        storeConfig: {},
        loading: false,
      },
    })

    await wrapper.get('[data-testid="duplicate-draft-button"]').trigger('click')

    expect(wrapper.emitted('duplicateDraft')).toEqual([[row]])
    expect(writeText).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('复制草稿')
  })

  it('草稿箱按待发布、发布中、发布失败和已发布区分展示并支持搜索', async () => {
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [
          draft('category', 'category_ready', '类目已选择'),
          draft('future', 'awaiting_review', '待人工复核'),
          { ...draft('publishing', 'publishing', '正在发布商品'), publishState: 'publishing' },
          { ...draft('failed', 'failed', '发布失败商品'), publishState: 'publish_failed' },
          { ...draft('published', 'published', '已发布商品'), publishState: 'published' },
        ],
        platformOptions: [],
        storeConfig: {},
        loading: false,
      },
    })

    // 默认在「待发布」Tab
    expect(wrapper.text()).toContain('类目已选择')
    expect(wrapper.text()).toContain('待人工复核')
    expect(wrapper.text()).not.toContain('正在发布商品')
    expect(wrapper.text()).not.toContain('发布失败商品')
    expect(wrapper.text()).not.toContain('已发布商品')
    expect(wrapper.text()).toContain('待发布：2')
    expect(wrapper.text()).toContain('发布中：1')
    expect(wrapper.text()).toContain('发布失败：1')
    expect(wrapper.text()).toContain('已发布：1')

    await wrapper.get('[data-testid="draft-tab-publishing"]').trigger('click')
    expect(wrapper.get('tbody').text()).toContain('正在发布商品')
    expect(wrapper.get('tbody').text()).not.toContain('已发布商品')

    await wrapper.get('[data-testid="draft-tab-failed"]').trigger('click')
    expect(wrapper.get('tbody').text()).toContain('发布失败商品')
    expect(wrapper.get('tbody').text()).not.toContain('正在发布商品')

    await wrapper.get('[data-testid="draft-tab-published"]').trigger('click')
    expect(wrapper.get('tbody').text()).toContain('已发布商品')
    expect(wrapper.get('tbody').text()).not.toContain('类目已选择')

    await wrapper.get('[data-testid="draft-tab-all"]').trigger('click')
    await wrapper.get('input[type="search"]').setValue('发布失败')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.get('tbody').text()).toContain('发布失败商品')
  })

  it('通过共享的两个选择器分别更新语言和市场', async () => {
    const row = draft('editable', 'claimed', '可编辑草稿')
    row.site = 'CBT'
    row.targetSites = [{
      platform: 'mercadolibre',
      site: 'CBT',
      language: 'es',
      listingCurrency: 'USD',
      sitesToSell: [{ siteId: 'MLM', logisticType: 'remote' }],
    }]
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [row],
        platformOptions,
        storeConfig,
        loading: false,
      },
    })

    await wrapper.get('[data-testid="draft-language-select"]').setValue('pt-BR')
    expect(wrapper.emitted('updateLanguage')).toEqual([[row, 'pt-BR']])

    await wrapper.get('[data-testid="draft-market-select-button"]').trigger('click')
    const checkboxes = wrapper.findAll('[data-testid="draft-target-checkbox"]')
    await checkboxes[1].setValue(true)
    await wrapper.get('[data-testid="draft-market-select-save"]').trigger('click')

    expect(wrapper.emitted('updateTargets')?.[0]?.[0]).toEqual(row)
    expect(wrapper.emitted('updateTargets')?.[0]?.[1]).toEqual([
      expect.objectContaining({
        platform: 'mercadolibre',
        site: 'CBT',
        language: 'es',
        sitesToSell: [
          { siteId: 'MLM', logisticType: 'remote' },
          { siteId: 'MCO', logisticType: 'remote' },
        ],
      }),
    ])
  })

  it('发布中草稿禁止删除，删除已发布草稿需弹窗二次确认', async () => {
    const publishingRow = { ...draft('pub-ing', 'publishing', '发布中商品'), publishState: 'publishing' as const }
    const publishedRow = { ...draft('pub-done', 'published', '已发布商品'), publishState: 'published' as const }
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [publishingRow, publishedRow],
        platformOptions: [],
        storeConfig: {},
        loading: false,
      },
      global: { stubs: { Teleport: true } },
    })

    await wrapper.get('[data-testid="draft-tab-publishing"]').trigger('click')
    const publishingDeleteBtn = wrapper.get('[data-testid="delete-draft-button"]')
    expect(publishingDeleteBtn.attributes('disabled')).toBeDefined()
    await publishingDeleteBtn.trigger('click')
    expect(wrapper.emitted('deleteDraft')).toBeUndefined()

    await wrapper.get('[data-testid="draft-tab-published"]').trigger('click')
    await wrapper.get('[data-testid="delete-draft-button"]').trigger('click')
    expect(wrapper.emitted('deleteDraft')).toBeUndefined()
    expect(wrapper.text()).toContain('确认删除已发布草稿')
    expect(wrapper.text()).toContain('不会下架或修改平台已发布的线上商品')

    await wrapper.get('[data-testid="confirm-delete-published-draft"]').trigger('click')
    expect(wrapper.emitted('deleteDraft')).toEqual([[publishedRow]])
  })

  it('支持勾选导出草稿 ZIP 与导入 ZIP 校验', async () => {
    const row = draft('draft-exp-1', 'claimed', '待导出草稿')
    const wrapper = mount(DraftBoxPanel, {
      props: {
        drafts: [row],
        platformOptions: [],
        storeConfig: {},
        loading: false,
        notice: '已成功导入 1 份草稿',
      },
    })

    expect(wrapper.text()).toContain('已成功导入 1 份草稿')
    const exportBtn = wrapper.get('[data-testid="drafts-export-button"]')
    expect(exportBtn.attributes('disabled')).toBeDefined()

    await wrapper.get('[aria-label="勾选草稿"]').setValue(true)
    expect(exportBtn.attributes('disabled')).toBeUndefined()
    await exportBtn.trigger('click')
    expect(wrapper.emitted('exportDrafts')).toEqual([[['draft-exp-1']]])

    const input = wrapper.get('[data-testid="drafts-import-input"]')
    const badFile = new File(['hello'], 'notes.txt', { type: 'text/plain' })
    Object.defineProperty(input.element, 'files', { value: [badFile], configurable: true })
    await input.trigger('change')
    expect(wrapper.emitted('importDrafts')).toBeUndefined()
    expect(wrapper.text()).toContain('请选择从草稿箱导出的 ZIP 压缩包文件')

    const zipFile = new File([new Uint8Array([80, 75, 3, 4])], 'drafts.zip', { type: 'application/zip' })
    Object.defineProperty(input.element, 'files', { value: [zipFile], configurable: true })
    await input.trigger('change')
    expect(wrapper.emitted('importDrafts')).toHaveLength(1)
    expect(wrapper.emitted('importDrafts')?.[0]?.[0]).toBe(zipFile)
  })
})
