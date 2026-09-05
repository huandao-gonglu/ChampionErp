import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CollectView from '../CollectView.vue'
import type { CollectDiagnostics, CollectForm, Product } from '@/types/workflow'

const form: CollectForm = {
  productUrl: '',
  productUrls: '',
  platform: '1688',
  mode: 'browser',
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

  it('Cookie 只保留在组件局部态并在提交后立即清空', async () => {
    const collectForm = { ...form, productUrl: 'https://detail.1688.com/offer/1.html' }
    const wrapper = mount(CollectView, {
      props: {
        form: collectForm,
        diagnostics,
        product,
        loading: false,
        error: '',
        batchRows: [],
        browserStatus: null,
      },
    })

    await wrapper.get('[data-testid="collect-method-url"]').trigger('click')
    const advancedButton = wrapper.findAll('button').find((button) => (
      button.text().includes('高级选项：Cookie')
    ))
    expect(advancedButton).toBeTruthy()
    await advancedButton!.trigger('click')

    const cookieInput = wrapper.get('[data-testid="transient-alibaba-cookie"]')
    await cookieInput.setValue('component-only-cookie-secret')
    expect(JSON.stringify(collectForm)).not.toContain('component-only-cookie-secret')

    const collectButton = wrapper.findAll('button').find((button) => (
      button.text() === '采集单链接'
    ))
    expect(collectButton).toBeTruthy()
    await collectButton!.trigger('click')

    expect(wrapper.emitted('collect')?.at(-1)?.[0]).toEqual({
      alibabaCookie: 'component-only-cookie-secret',
    })
    expect((cookieInput.element as HTMLTextAreaElement).value).toBe('')
    expect(JSON.stringify(collectForm)).not.toContain('component-only-cookie-secret')
  })
  it('等待验证时说明自动继续，并允许在加载期间取消等待', async () => {
    const wrapper = mount(CollectView, { props: { form: { ...form }, diagnostics: { ...diagnostics, status: 'waiting_verification' }, product, loading: true, error: '', batchRows: [], browserStatus: null } })
    const cancel = wrapper.findAll('button').find(button => button.text() === '取消等待')!
    expect(cancel.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('完成后会自动采集这一项')
    await cancel.trigger('click')
    expect(wrapper.emitted('cancelVerification')).toHaveLength(1)
  })

})
