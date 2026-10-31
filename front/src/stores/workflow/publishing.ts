import { ref } from 'vue'
import { defineStore } from 'pinia'
import { createDefaultPricingInput } from '@/constants/initialState'
import type {
  CategoryAttributeTranslations,
  CategoryPrecheckResult,
  CategoryResultTranslations,
  CategorySearchResult,
  CategorySelection,
  Marketplace,
  MarketplaceOption,
  PayloadPreviewState,
  PricingInput,
  PricingResult,
  PublishJob,
  PublishPrecheck,
  UnknownRecord,
} from '@/types/workflow'

export const useWorkflowPublishingStore = defineStore('workflow-publishing', () => {
  const pricingInput = ref<PricingInput>(createDefaultPricingInput())
  const pricingResult = ref<PricingResult | null>(null)
  const category = ref<CategorySelection | null>(null)
  const categoryQuery = ref('')
  const categoryResults = ref<CategorySearchResult[]>([])
  const categoryRecommendations = ref<Record<string, { query: string; results: CategorySearchResult[]; error: string }>>({})
  const categoryAutoMatching = ref(false)
  const categoryAutoMatchMessage = ref('')
  const categoryAutoMatchCurrent = ref(0)
  const categoryAutoMatchTotal = ref(0)
  const categoryAutoMatchProductName = ref('')
  const categoryAttributeTranslations = ref<CategoryAttributeTranslations>({})
  const categoryAttributeTranslationsSource = ref('')
  const categoryAttributeTranslating = ref(false)
  const categoryAttributeLoading = ref(false)
  const categoryAttributeError = ref('')
  const categoryResultTranslations = ref<CategoryResultTranslations>({})
  const categoryResultTranslationsSource = ref('')
  const categoryResultTranslating = ref(false)
  const categoryPrecheck = ref<CategoryPrecheckResult | null>(null)
  const precheck = ref<PublishPrecheck | null>(null)
  const precheckResults = ref<UnknownRecord>({})
  const payloadPreview = ref<PayloadPreviewState | null>(null)
  const copyGenerating = ref(false)
  const publishJob = ref<PublishJob | null>(null)
  const activeMarketplace = ref<Marketplace>('mercadolibre')
  const platformOptions = ref<MarketplaceOption[]>([])
  const publishResult = ref<UnknownRecord | null>(null)
  const activePublishTargetKey = ref('')

  return {
    pricingInput,
    pricingResult,
    category,
    categoryQuery,
    categoryResults,
    categoryRecommendations,
    categoryAutoMatching,
    categoryAutoMatchMessage,
    categoryAutoMatchCurrent,
    categoryAutoMatchTotal,
    categoryAutoMatchProductName,
    categoryAttributeTranslations,
    categoryAttributeTranslationsSource,
    categoryAttributeTranslating,
    categoryAttributeLoading,
    categoryAttributeError,
    categoryResultTranslations,
    categoryResultTranslationsSource,
    categoryResultTranslating,
    categoryPrecheck,
    precheck,
    precheckResults,
    payloadPreview,
    copyGenerating,
    publishJob,
    activeMarketplace,
    platformOptions,
    publishResult,
    activePublishTargetKey,
  }
})
