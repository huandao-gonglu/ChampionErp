import { afterEach, describe, expect, it, vi } from 'vitest'
import { AxiosError } from 'axios'
import { apiClient } from '../client'
import { SESSION_EXPIRED_EVENT } from '@/utils/accountTransport'

const defaultAdapter = apiClient.defaults.adapter
afterEach(() => { apiClient.defaults.adapter = defaultAdapter })

describe('业务 API 错误传输', () => {
  it('改密和退出的 401 通知会话失效，登录密码错误仍在登录页处理', async () => {
    const expired = vi.fn()
    window.addEventListener(SESSION_EXPIRED_EVENT, expired)
    apiClient.defaults.adapter = async config => {
      throw new AxiosError('需要登录', 'ERR_BAD_REQUEST', config, undefined, {
        config, status: 401, statusText: 'Unauthorized', headers: {}, data: { error: '需要登录' },
      })
    }
    try {
      await apiClient.post('/api/account/login', {}).catch(() => {})
      expect(expired).not.toHaveBeenCalled()
      await apiClient.post('/api/account/password', {}).catch(() => {})
      await apiClient.post('/api/account/logout', {}).catch(() => {})
      expect(expired).toHaveBeenCalledTimes(2)
    } finally { window.removeEventListener(SESSION_EXPIRED_EVENT, expired) }
  })
  it('保留服务端原始响应、错误码和状态，业务层可读取冲突任务编号', async () => {
    const data = {ok:false,error:'该商品已有未完成的修改',error_code:'ONLINE_CONFLICT',conflict_job_id:'blocking-job'}
    apiClient.defaults.adapter = async config => {
      throw new AxiosError('Request failed with status code 409', 'ERR_BAD_REQUEST', config, undefined, {
        config, status:409, statusText:'Conflict', headers:{}, data,
      })
    }
    const error = await apiClient.post('/api/online-products/change', {}).catch(error => error)
    expect(error).toBeInstanceOf(Error)
    expect(error).toMatchObject({message:data.error,code:'ONLINE_CONFLICT',status:409})
    expect(error.raw).toBe(data)
  })

  it('无 HTTP 响应时仍保留网络错误和原始异常', async () => {
    const original = new AxiosError('网络连接失败', 'ERR_NETWORK')
    apiClient.defaults.adapter = async () => { throw original }
    const error = await apiClient.get('/api/online-products').catch(error => error)
    expect(error).toBeInstanceOf(Error)
    expect(error.message).toBe('网络连接失败')
    expect(error.status).toBeUndefined()
    expect(error.raw).toBe(original)
  })
})
