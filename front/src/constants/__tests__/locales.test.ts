import { describe, expect, it } from 'vitest'
import {
  LISTING_LANGUAGE_OPTIONS,
  MARKETPLACE_LISTING_LOCALES,
  listingLanguageLabel,
} from '@/constants/locales'

describe('刊登语言约定', () => {
  it('Mercado Libre Global 默认使用英文且语言选项不重复', () => {
    expect(MARKETPLACE_LISTING_LOCALES.mercadolibre).toMatchObject({
      value: 'en-US',
      label: 'English',
    })
    expect(listingLanguageLabel('mercadolibre')).toBe('English')
    const values = LISTING_LANGUAGE_OPTIONS.map((item) => item.value)
    expect(new Set(values).size).toBe(values.length)
    expect(values).toContain('es')
  })
})
