import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { translateText } from '@/api/workflow/translation'

vi.mock('@/api/client', () => ({
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
      { presentationId: 'presentation-translation' },
    )

    expect(apiClient.post).toHaveBeenCalledWith('/api/text-translate', {
      target_language: 'zh-CN',
      content: {
        'category.0.path': 'Hogar / Ventiladores',
        'category.1.path': 'Computación / Accesorios',
      },
    }, { aiPresentationId: 'presentation-translation' })
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
})
