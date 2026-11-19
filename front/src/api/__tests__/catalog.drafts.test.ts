import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { fetchDraftsIndex } from '@/api/workflow/catalog'

vi.mock('@/api/client', () => ({ API_REQUEST_TIMEOUT_MS: 30000, apiClient: { get: vi.fn() } }))

describe('草稿箱列表加载', () => {
  beforeEach(() => vi.clearAllMocks())

  it('默认同时加载未发布和已发布草稿，供分区筛选', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { ok: true, items: [
      { draft_id: 'draft-active', status: 'claimed' },
      { draft_id: 'draft-submitted', status: 'published', publish_status: 'pending_confirmation' },
    ] } })
    const rows = await fetchDraftsIndex()
    expect(apiClient.get).toHaveBeenCalledWith('/api/drafts-index?scope=all&view=list')
    expect(rows.map(row => row.status)).toEqual(['claimed', 'published'])
    expect(rows[1].publishStatus).toBe('pending_confirmation')
  })
})
