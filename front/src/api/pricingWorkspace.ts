import { apiClient } from '@/api/client'
import type { PricingExchangeRates, PricingTemplateParameters, PricingTemplateSettings } from '@/types/pricing'
import { ensureOk } from '@/api/workflow/normalizers'

export async function fetchPricingExchangeRates(refresh = false): Promise<PricingExchangeRates> {
  const { data } = await apiClient.get('/api/pricing/exchange-rates', { params: refresh ? { refresh: '1' } : undefined })
  ensureOk(data, '读取实时汇率失败')
  return data
}

export async function fetchPricingTemplates(): Promise<PricingTemplateSettings> {
  const { data } = await apiClient.get('/api/pricing/templates')
  ensureOk(data, '读取核价模板失败')
  return data
}

export async function savePricingTemplate(template: { id?: string; name: string; parameters: PricingTemplateParameters; set_default: boolean }): Promise<PricingTemplateSettings> {
  const { data } = await apiClient.post('/api/pricing/templates/save', template)
  ensureOk(data, '保存核价模板失败')
  return data
}

export async function setDefaultPricingTemplate(id: string): Promise<PricingTemplateSettings> {
  const { data } = await apiClient.post('/api/pricing/templates/default', { id })
  ensureOk(data, '设置默认模板失败')
  return data
}

export async function deletePricingTemplate(id: string): Promise<PricingTemplateSettings> {
  const { data } = await apiClient.post('/api/pricing/templates/delete', { id })
  ensureOk(data, '删除核价模板失败')
  return data
}
