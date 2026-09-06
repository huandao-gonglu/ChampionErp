import { computed, reactive, watch } from 'vue'
import type { DraftPublishOperation } from '@/types/workflow'
import { publishPrecheckPassed } from '@/utils/publishReadiness'
import type { createDraftTargetEditors } from './targetEditors'
import type { WorkflowRuntime } from './runtime'

type TargetEditors = ReturnType<typeof createDraftTargetEditors>
type TargetEditor = TargetEditors['value'][number]

/** 批次只编排现有单目标业务动作，共享草稿的写入依次执行。 */
export function createDraftPublishBatch(runtime: WorkflowRuntime, editors: TargetEditors) {
  const progress = reactive({ operation: '' as DraftPublishOperation | '', completed: 0, total: 0, message: '', error: '' })
  const pending = computed(() => editors.value.filter((editor) => !editor.state.queuedPublishJobId))
  const passed = computed(() => pending.value.filter((editor) => publishPrecheckPassed(editor.state.precheck)))
  const ready = computed(() => passed.value.filter((editor) => (
    !editor.state.publishFailure && editor.state.payloadPreview?.validationDigest && editor.state.payloadPreview.targetKey === editor.key
  )))
  const queuedCount = computed(() => editors.value.filter((editor) => editor.state.queuedPublishJobId).length)
  const labels: Record<DraftPublishOperation, string> = { precheck: '预检', preview: '准备预览', publish: '加入队列' }
  let generation = 0
  watch(() => runtime.currentDraft.value.draftId, () => {
    generation += 1
    Object.assign(progress, { operation: '', completed: 0, total: 0, message: '', error: '' })
  }, { flush: 'sync' })

  async function process(editor: TargetEditor, operation: DraftPublishOperation) {
    editor.state.publishFailure = null
    editor.state.error = ''
    if (operation === 'precheck') {
      await editor.execute(() => editor.actions.runPrecheck({ saveDraft: false }))
    } else if (operation === 'preview') {
      await editor.execute(() => editor.actions.previewPayload({ saveDraft: false }))
    } else {
      const job = await editor.execute(editor.actions.enqueuePublish)
      if (job && editor.attached()) editor.state.queuedPublishJobId = job.jobId
    }
    if (!editor.attached()) return
    const success = !editor.state.error && (operation === 'publish'
      ? Boolean(editor.state.queuedPublishJobId)
      : publishPrecheckPassed(editor.state.precheck) && (operation !== 'preview' || Boolean(editor.state.payloadPreview?.validationDigest && editor.state.payloadPreview.targetKey === editor.key)))
    if (!success) {
      editor.state.publishFailure = {
        operation,
        message: editor.state.error || (operation === 'precheck'
          ? '预检未通过，请查看下方问题清单。'
          : `${labels[operation]}未完成，请查看下方该市场的检查结果。`),
      }
    }
  }

  async function run(operation: DraftPublishOperation, candidates: TargetEditor[]) {
    if (runtime.loading.value || progress.operation || !candidates.length) return
    const currentGeneration = generation
    // 点击确认时固定目标及指纹，执行过程中新增的候选不能被隐式发布。
    const targets = candidates.map((editor) => ({ editor, digest: editor.state.payloadPreview?.validationDigest }))
    runtime.loading.value = true
    Object.assign(progress, { operation, completed: 0, total: targets.length, error: '', message: '' })
    try {
      if (operation !== 'publish') {
        // 每轮只保存一次；确认入队阶段不保存、不重新生成已确认的 Payload。
        if (operation === 'precheck') for (const { editor } of targets) editor.actions.invalidatePublishValidation()
        if (operation === 'preview') for (const { editor } of targets) editor.state.payloadPreview = null
        await runtime.persistCurrentDraftForPublish()
      }
      for (const { editor, digest } of targets) {
        if (currentGeneration !== generation) break
        if (!editor.attached() || editor.state.queuedPublishJobId) continue
        const platform = runtime.platformOptions.value.find((item) => item.key === editor.target.platform)?.label || editor.target.platform
        progress.message = `正在${labels[operation]}：${platform} · ${editor.target.site}`
        if (operation === 'publish' && (
          !publishPrecheckPassed(editor.state.precheck) || !digest || editor.state.payloadPreview?.validationDigest !== digest
        )) {
          editor.state.publishFailure = { operation, message: '发布资料已变化，请重新准备预览后再确认。' }
        } else {
          await process(editor, operation)
        }
        if (currentGeneration !== generation) break
        progress.completed += 1
      }
    } catch (error) {
      if (currentGeneration === generation) progress.error = error instanceof Error ? error.message : '批量发布准备失败。'
    } finally {
      if (currentGeneration === generation) {
        progress.operation = ''
        progress.message = progress.error ? '' : `已处理 ${progress.completed} / ${progress.total} 个目标，请查看各市场结果。`
      }
      runtime.loading.value = false
    }
  }

  function retry(editor: TargetEditor) {
    if (!editor.state.publishFailure || editor.state.queuedPublishJobId) return
    const operation = editor.state.publishFailure.operation === 'precheck' || !publishPrecheckPassed(editor.state.precheck)
      ? 'precheck' : 'preview'
    // 发布失败也先重新预览，继续通过统一确认按钮提交。
    return run(operation, [editor])
  }

  return reactive({
    progress, pending, passed, ready, queuedCount,
    precheckAll: () => run('precheck', pending.value),
    previewAll: () => run('preview', passed.value),
    enqueueAll: () => run('publish', ready.value),
    retry,
  })
}
