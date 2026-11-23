import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { isNonTranslatableText, translateText, translateTextSilent } from '@/api/workflow/translation'

vi.mock('@/api/client', () => ({
  API_REQUEST_TIMEOUT_MS: 180_000,
  apiClient: {
    post: vi.fn(),
  },
}))

describe('workflow translation API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends language and flat content to the generic translation endpoint', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        ok: true,
        translations: {
          'category.0.path': '家居 / 风扇',
          'category.1.path': '电脑 / 配件',
        },
      },
    })

    const result = await translateText(
      'zh-CN',
      {
        'category.0.path': 'Hogar / Ventiladores',
        'category.1.path': 'Computación / Accesorios',
      },
      'conversation-translation',
    )

    expect(apiClient.post).toHaveBeenCalledWith('/api/text-translate', {
      target_language: 'zh-CN',
      content: {
        'category.0.path': 'Hogar / Ventiladores',
        'category.1.path': 'Computación / Accesorios',
      },
    }, { aiConversationId: 'conversation-translation' })
    expect(result).toEqual({
      'category.0.path': '家居 / 风扇',
      'category.1.path': '电脑 / 配件',
    })
  })

  it('rejects missing or renamed response keys', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        ok: true,
        translations: { renamed: '品牌' },
      },
    })

    await expect(translateText('zh-CN', {
      'attribute.0.label': 'Marca',
    })).rejects.toThrow('翻译结果与请求内容不一致')
  })

  it('多批文本共用一次请求及对话，按批次数留出处理时间', async () => {
    const content = Object.fromEntries(Array.from({ length: 101 }, (_, index) => [String(index), 'Fan']))
    vi.mocked(apiClient.post).mockResolvedValue({ data: { ok: true, translations: content } })

    await expect(translateText('zh-CN', content, 'conversation-translation')).resolves.toEqual(content)
    expect(apiClient.post).toHaveBeenCalledOnce()
    expect(apiClient.post).toHaveBeenCalledWith('/api/text-translate', { target_language: 'zh-CN', content },
      { aiConversationId: 'conversation-translation', timeout: 360_000 })
  })

  it('translateTextSilent 不开启 AI 会话并在失败时静默降级', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, translations: { a: '品牌' }, source: 'cache', degraded: false },
    })
    const res = await translateTextSilent('zh-CN', { a: 'Marca' }, { domain: 'mercadolibre' })
    expect(res).toEqual({ translations: { a: '品牌' }, source: 'cache', degraded: false })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/text-translate',
      { domain: 'mercadolibre', target_language: 'zh-CN', content: { a: 'Marca' }, cache_only: false, silent: true },
      {},
    )

    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('network down'))
    const degraded = await translateTextSilent('zh-CN', { a: 'Marca' }, { domain: 'mercadolibre' })
    expect(degraded).toEqual({ translations: {}, source: 'cache', degraded: true })
  })

  it('isNonTranslatableText 跳过中文、数字单位与短型号码', () => {
    expect(isNonTranslatableText('品牌')).toBe(true)
    expect(isNonTranslatableText('500g')).toBe(true)
    expect(isNonTranslatableText('XXL')).toBe(true)
    expect(isNonTranslatableText('USB-C')).toBe(true)
    expect(isNonTranslatableText('Цвет товара')).toBe(false)
  })
})
