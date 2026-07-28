import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CollectView from '../CollectView.vue'
import type { CollectDiagnostics, CollectForm, Product } from '@/types/workflow'

const form: CollectForm = {
  productUrl: '',
  productUrls: '',
  platform: '1688',
  mode: 'browser',
  alibabaCookie: '',
  alibabaAppKey: '',
  alibabaAppSecret: '',
  alibabaAccessToken: '',
  alibabaApiBaseUrl: '',
  alibabaApiMethod: '',
  alibabaApiVersion: '',
  alibabaApiTimeoutSeconds: '',
  autoAiRecognition: false,
  outputDir: '',
  selectedClaimPlatforms: [],
  manualTitle: '',
  manualPrice: '',
  manualBullets: '',
  manualDescription: '',
  manualDimensions: '',
  manualWeight: '',
  manualImages: '',
  rawText: '',
}

const diagnostics: CollectDiagnostics = {
  status: 'idle',
  progress: 0,
  message: '等待采集',
  downloadedImages: 0,
  extractedBullets: 0,
  antiBotWarning: false,
  lastSourceUrl: '',
  errorCode: '',
  nextAction: '',
  htmlSnapshotPath: '',
  screenshotPath: '',
  raw: {},
}

const product = {
  name: '',
  source: {
    sourcePlatform: '',
    title: '',
    price: '',
    currency: '',
    description: '',
    dimensions: {
      lengthCm: '',
      widthCm: '',
      heightCm: '',
    },
    weightKg: '',
  },
} as Product

describe('CollectView', () => {
  it('采集页面的主要卡片使用项目统一表面色', () => {
    const wrapper = mount(CollectView, {
      props: {
        form: { ...form },
        diagnostics,
        product,
        loading: false,
        error: '',
        batchRows: [],
        browserStatus: null,
      },
    })

    for (const testId of [
      'collect-method-card',
      'collect-active-card',
      'collect-diagnostics-card',
      'collect-result-card',
    ]) {
      const classes = wrapper.get(`[data-testid="${testId}"]`).classes()
      expect(classes).toContain('card')
      expect(classes).not.toContain('bg-primary-50/70')
      expect(classes).not.toContain('bg-primary-500/10')
      expect(classes).not.toContain('bg-blue-50/70')
      expect(classes).not.toContain('bg-blue-500/10')
    }
  })
})
