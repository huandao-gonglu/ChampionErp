import type { CategoryAttributeDefinition, CategorySelection, Marketplace } from '@/types/workflow'

const barcodeIds = new Set(['GTIN', 'UPC', 'UNIVERSAL_PRODUCT_CODE'])
export const DEFAULT_EMPTY_GTIN_REASON = 'The product does not have registered code'

export function isBarcodeIdentifier(attributeId: string): boolean {
  return barcodeIds.has(attributeId.toUpperCase())
}

export function barcodeAttributes(platform: Marketplace, category: CategorySelection | null | undefined): CategoryAttributeDefinition[] {
  if (!category || category.platform !== platform) return []
  return [...category.requiredAttributes, ...(category.optionalAttributes || [])].filter(attribute => (
    !attribute.readOnly && (isBarcodeIdentifier(attribute.id)
      ? platform === 'mercadolibre' || attribute.required
      : platform === 'mercadolibre' && attribute.id === 'EMPTY_GTIN_REASON')
  ))
}
