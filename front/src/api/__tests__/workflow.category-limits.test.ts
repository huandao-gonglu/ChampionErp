import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { fetchCategoryAttrs } from '@/api/workflow/publishing'
vi.mock('@/api/client', () => ({ API_REQUEST_TIMEOUT_MS: 30000, apiClient: { post: vi.fn() } }))
const page = (attributes: unknown[], has_more = false, next_cursor = '') => ({ data: {
  ok: true, platform: 'yandex', category_id: '70063695', site: 'global', attributes, has_more, next_cursor,
} })
beforeEach(() => vi.resetAllMocks())
describe('草稿属性完整分页与规则', () => {
  it('读取第 101 项及以后规则，保留零和缺失的区别', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page(Array.from({ length: 100 }, (_, id) => ({ id: String(id), name: String(id) })), true, 'offset:100'))
      .mockResolvedValueOnce(page([{ id: '23476910', name: 'Версия', max_length: 100 }, { id: 'numeric', min_value: '0', max_value: '1.5' }]))
    const result = await fetchCategoryAttrs('yandex', '70063695', 'global')
    expect(result.optionalAttributes).toHaveLength(102)
    expect(result.optionalAttributes[100]).toMatchObject({ id: '23476910', maxLength: 100 })
    expect(result.optionalAttributes[101]).toMatchObject({ minValue: '0', maxValue: '1.5' })
    expect(result.optionalAttributes[0]?.maxLength).toBeUndefined()
    expect(apiClient.post).toHaveBeenNthCalledWith(2, '/api/category-attrs', expect.objectContaining({ cursor: 'offset:100', limit: 100 }))
  })
  it.each(['missing', 'repeated', 'identity', 'failure', 'duplicate', 'site', 'no_attributes'])('拒绝不完整分页：%s', async (reason) => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([{ id: '1' }], true, reason === 'missing' ? '' : 'next'))
    if (reason === 'failure') vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('平台读取失败'))
    else vi.mocked(apiClient.post).mockResolvedValueOnce(reason === 'identity'
      ? { data: { ...page([{ id: '2' }]).data, category_id: 'other' } }
      : reason === 'site' ? { data: { ...page([{ id: '2' }]).data, site: 'different' } }
      : reason === 'no_attributes' ? { data: { ...page([]).data, attributes: undefined } }
      : page([{ id: reason === 'duplicate' ? '1' : '2' }], reason === 'repeated', 'next'))
    await expect(fetchCategoryAttrs('yandex', '70063695', 'global')).rejects.toThrow()
    expect(apiClient.post).toHaveBeenCalledTimes(reason === 'missing' ? 1 : 2)
  })
  it('接受平台规范化站点，后续分页必须保持相同站点', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce(page([{ id: '1' }], true, 'next'))
      .mockResolvedValueOnce(page([{ id: '2' }]))
    expect((await fetchCategoryAttrs('yandex', '70063695', 'RU')).optionalAttributes).toHaveLength(2)
  })

})
