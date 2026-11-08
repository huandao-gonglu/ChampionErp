import { onScopeDispose, shallowRef } from 'vue'
import { createDesktopHandoff, fetchDesktopHandoffStatus, confirmDesktopHandoff, desktopBackendOrigin } from '@/api/desktopHandoff'
import { hasLocalOperations, modelRequiresDesktop } from './desktop'
import type { CollectBatchRow, DraftDetail, DraftProductContext, PricingInput, Product, UnknownRecord } from '@/types/workflow'
import type { FileUIPart } from 'ai'

export interface DesktopWorkflowState {
  draft?: DraftDetail
  draftContext?: DraftProductContext
  draftTab?: 'text' | 'category' | 'images' | 'pricing' | 'skus' | 'precheck'
  pricingInput?: PricingInput
  product?: Product
  editorMode?: 'text' | 'images'
  collect?: { mode: string; productUrl: string; platform: string }
  collectTab?: 'manual' | 'browser' | 'api' | 'url'
  batchRows?: CollectBatchRow[]
}

export interface DesktopHandoffContext {
  target?: string
  state?: DesktopHandoffState
}
export interface DesktopChatState {
  text: string
  attachments: { part: FileUIPart; size: number }[]
  conversationId?: string | null
  floating?: boolean
  draftIds?: string[]
}
export interface DesktopHandoffState extends Record<string, unknown> {
  workflow?: DesktopWorkflowState
  chat?: DesktopChatState
  modelSettings?: { selectedModelId?: string; model?: UnknownRecord; connectionType?: string | boolean; browserPresetId?: string }
}
export interface ReceivedDesktopHandoff {
  requestId: string
  target: string
  state: DesktopHandoffState
}

export const desktopLaunch = shallowRef<{ requestId: string; url: string; status: 'opening' | 'waiting' | 'opened' | 'error'; error?: string } | null>(null)
export const receivedDesktopHandoff = shallowRef<ReceivedDesktopHandoff | null>(null)
const contexts: (() => DesktopHandoffContext | null)[] = []
let revision = 0
let timer: ReturnType<typeof setTimeout> | undefined
let lastContext: DesktopHandoffContext = {}
let completingRequest = ''
const completedParts = new Set<string>()

export function useDesktopHandoffContext(context: () => DesktopHandoffContext | null) {
  contexts.push(context)
  onScopeDispose(() => contexts.splice(contexts.indexOf(context), 1))
}

export function dismissDesktopLaunch() {
  ++revision
  clearTimeout(timer)
  desktopLaunch.value = null
  lastContext = {}
}

export function resetDesktopHandoff() {
  dismissDesktopLaunch()
  receivedDesktopHandoff.value = null
}

export async function launchDesktop(context: DesktopHandoffContext = {}) {
  if (hasLocalOperations() || desktopLaunch.value?.status === 'opening') return
  const token = ++revision
  clearTimeout(timer)
  const base = contexts.map(provider => provider()).filter(Boolean).at(-1) || {}
  const fallbackTarget = ['/', '/aiWork'].includes(window.location.pathname) ? window.location.pathname + window.location.search : '/aiWork'
  const target = context.target || base.target || fallbackTarget
  const state = context.target && context.target !== base.target ? context.state || {} : { ...base.state, ...context.state }
  lastContext = JSON.parse(JSON.stringify({ target, state })) as DesktopHandoffContext
  desktopLaunch.value = { requestId: '', url: '', status: 'opening' }
  try {
    const data = await createDesktopHandoff(target, state)
    if (token !== revision) return
    const origin = desktopBackendOrigin()
    const url = `champion-erp://continue?request=${encodeURIComponent(data.request_id)}&origin=${encodeURIComponent(origin)}`
    desktopLaunch.value = { requestId: data.request_id, url, status: 'opening' }
    window.location.assign(url)
    const started = Date.now()
    async function check() {
      if (token !== revision) return
      try {
        const result = await fetchDesktopHandoffStatus(data.request_id)
        if (token !== revision) return
        if (result.opened) {
          desktopLaunch.value = { requestId: data.request_id, url, status: 'opened' }
          timer = setTimeout(() => { if (token === revision) dismissDesktopLaunch() }, 4000)
          return
        }
      } catch (error) {
        if (token !== revision) return
        if ((error as { status?: number }).status === 410) {
          desktopLaunch.value = { requestId: data.request_id, url, status: 'error', error: '打开请求已过期，请重试。' }
          return
        }
      }
      if (token !== revision) return
      if (Date.now() - started >= 5000) desktopLaunch.value = { requestId: data.request_id, url, status: 'waiting' }
      if (Date.now() - started < data.expires_in * 1000) timer = setTimeout(check, 1500)
      else desktopLaunch.value = { requestId: data.request_id, url, status: 'error', error: '打开请求已过期，请重试。' }
    }
    timer = setTimeout(check, 1000)
  } catch (error) {
    if (token === revision) desktopLaunch.value = { requestId: '', url: '', status: 'error', error: error instanceof Error ? error.message : '暂时无法打开桌面端，请重试。' }
  }
}

export function retryDesktopLaunch() {
  const current = desktopLaunch.value
  if (current?.url && current.status !== 'error') {
    window.location.assign(current.url)
  } else {
    void launchDesktop(lastContext)
  }
}

export function handoffModelOperation(useCase: string, context?: DesktopHandoffContext): boolean {
  if (!modelRequiresDesktop(useCase)) return false
  void launchDesktop(context)
  return true
}

export async function acknowledgeDesktopHandoff(requestId: string, part?: 'workflow' | 'chat' | 'modelSettings') {
  const received = receivedDesktopHandoff.value
  if (received?.requestId !== requestId) return
  if (completingRequest !== requestId) { completingRequest = requestId; completedParts.clear() }
  if (part) completedParts.add(part)
  if (['workflow', 'chat', 'modelSettings'].some(key => received.state[key] && !completedParts.has(key))) return
  await confirmDesktopHandoff(requestId)
  await window.championDesktop?.finishHandoff?.(requestId)
  if (receivedDesktopHandoff.value?.requestId === requestId) receivedDesktopHandoff.value = null
}
