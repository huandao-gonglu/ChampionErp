import { afterEach, describe, expect, it } from 'vitest'
import { AxiosError } from 'axios'
import { apiClient } from '../client'

const defaultAdapter = apiClient.defaults.adapter
afterEach(() => { apiClient.defaults.adapter = defaultAdapter })

describe('业务 API 错误传输', () => {
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
