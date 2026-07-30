import { describe, expect, it } from 'vitest'
import {
  isSensitiveConfigKey,
  sanitizePublicAppConfig,
} from '@/utils/configSecurity'

describe('configSecurity', () => {
  it.each([
    'vendorApiKey',
    'clientSecret',
    'access-token',
    'privateKey',
    'sourceKey',
  ])('recognizes normalized sensitive key %s', (key) => {
    expect(isSensitiveConfigKey(key)).toBe(true)
  })

  it('keeps masked summaries but clears replayable credential fields', () => {
    const safe = sanitizePublicAppConfig({
      provider: {
        vendorApiKey: 'vend...cret',
        clientSecret: 'clie...cret',
        'access-token': 'acce...oken',
        privateKey: 'priv...-key',
        maskedVendorApiKey: 'vend...cret',
      },
    })

    expect(safe).toEqual({
      provider: {
        vendorApiKey: '',
        clientSecret: '',
        'access-token': '',
        privateKey: '',
        maskedVendorApiKey: 'vend...cret',
      },
    })
  })
})
