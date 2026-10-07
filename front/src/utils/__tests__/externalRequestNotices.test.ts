import { beforeEach, describe, expect, it, vi } from 'vitest'
import { observeExternalOperations, observedExternalOperations, acceptExternalNotices, externalRequestNotice } from '../externalRequestNotices'
beforeEach(() => { externalRequestNotice.value = '' })
describe('用户请求中断提示', () => {
  it('即使领域响应改写错误也识别公共暂停响应头', () => {
    observeExternalOperations({ 'x-external-request-blocked': '1' }, 'post')
    expect(externalRequestNotice.value).toContain('未能完成')
    expect(externalRequestNotice.value).toContain('平台授权')
  })
  it('成功提交后台任务不提示，实际被拦截才提示且不重复', () => {
    observeExternalOperations({ 'x-external-operation-ids': 'http-request,job-1' }, 'post')
    expect(externalRequestNotice.value).toBe('')
    expect(observedExternalOperations()).toContain('job-1')
    acceptExternalNotices([{ operation_id: 'system-job', message: '后台暂停' }])
    expect(externalRequestNotice.value).toBe('')
    acceptExternalNotices([{ operation_id: 'job-1', message: '冷却中' }])
    expect(externalRequestNotice.value).toContain('冷却中')
    externalRequestNotice.value = ''
    acceptExternalNotices([{ operation_id: 'job-1', message: '冷却中' }])
    expect(externalRequestNotice.value).toBe('')
  })
  it('普通轮询不注册后台任务，过期关联自动移除', () => {
    observeExternalOperations({ 'x-external-operation-ids': 'poll,system-job' }, 'get')
    expect(observedExternalOperations()).not.toContain('system-job')
    observeExternalOperations({ 'x-external-operation-ids': 'http-request,job-expired' }, 'post')
    const original = Date.now()
    vi.spyOn(Date, 'now').mockReturnValue(original + 3600001)
    expect(observedExternalOperations()).not.toContain('job-expired')
    vi.restoreAllMocks()
  })
  it('重试同一任务排除旧失败，但仍提示本次新的失败', () => {
    observeExternalOperations({ 'x-external-operation-ids': 'http-request,same-job', 'x-external-operation-since': '100' }, 'post')
    acceptExternalNotices([{ operation_id: 'same-job', message: '首次暂停', created_at: 101 }])
    externalRequestNotice.value = ''
    observeExternalOperations({ 'x-external-operation-ids': 'http-retry,same-job', 'x-external-operation-since': '200' }, 'post')
    acceptExternalNotices([{ operation_id: 'same-job', message: '旧失败', created_at: 101 }])
    expect(externalRequestNotice.value).toBe('')
    acceptExternalNotices([{ operation_id: 'same-job', message: '新的暂停', created_at: 201 }])
    expect(externalRequestNotice.value).toContain('新的暂停')
  })

})
