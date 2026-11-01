import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../client'
import { fetchForbiddenCategories, importForbiddenCategories } from '../forbiddenCategories'

afterEach(() => vi.restoreAllMocks())
describe('禁售类目导入接口', () => {
  it('读取当前名单并上传原始文件内容，明确指定 Yandex', async () => {
    const result = { platform: 'yandex', filename: '禁售.xlsx', categories: [], imported_at: '' }
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: result })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: result })
    expect(await fetchForbiddenCategories()).toEqual(result)
    expect(get).toHaveBeenCalledWith('/api/system-settings/forbidden-categories')
    expect(await importForbiddenCategories(new File(['xlsx-content'], '禁售.xlsx'))).toEqual(result)
    expect(post).toHaveBeenCalledWith('/api/system-settings/forbidden-categories/import', {
      platform: 'yandex', filename: '禁售.xlsx', content_base64: btoa('xlsx-content'),
    })
  })
})
