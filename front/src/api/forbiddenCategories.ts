import { apiClient } from './client'
import type { BackendForbiddenCategories } from '@/types/workflow.generated'

export async function fetchForbiddenCategories(): Promise<BackendForbiddenCategories> {
  return (await apiClient.get<BackendForbiddenCategories>('/api/system-settings/forbidden-categories')).data
}

export async function importForbiddenCategories(file: File): Promise<BackendForbiddenCategories> {
  const content = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('无法读取禁售类目文件'))
    reader.readAsDataURL(file)
  })
  return (await apiClient.post<BackendForbiddenCategories>('/api/system-settings/forbidden-categories/import', {
    platform: 'yandex', filename: file.name, content_base64: content,
  })).data
}
