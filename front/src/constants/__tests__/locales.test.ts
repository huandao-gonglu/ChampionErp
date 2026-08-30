import { describe, expect, it } from 'vitest'
import {
  LISTING_LANGUAGE_OPTIONS,
  MARKETPLACE_LISTING_LOCALES,
  listingLanguageValue,
} from '@/constants/locales'

describe('刊登语言约定', () => {
  it('Mercado Libre 默认使用销售子市场语言且不包含 CBT 英语', () => {
    expect(MARKETPLACE_LISTING_LOCALES.mercadolibre).toMatchObject({
      value: 'es',
      label: 'Spanish',
    })
    expect(listingLanguageValue('mercadolibre')).toBe('es')
    const values = LISTING_LANGUAGE_OPTIONS.map((item) => item.value)
    expect(new Set(values).size).toBe(values.length)
    expect(values).toContain('es')
    expect(values).toContain('pt-BR')
    expect(values).not.toContain('en-US')
  })
})
