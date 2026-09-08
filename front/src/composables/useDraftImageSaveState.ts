import { computed, ref, watch, type Ref } from 'vue'
import { draftImageSignature } from '@/utils/draftImages'
import type { DraftDetail } from '@/types/workflow'

export function useDraftImageSaveState(draft: Ref<DraftDetail>, persist: () => Promise<boolean>) {
  const saved = ref(draftImageSignature(draft.value))
  const saving = ref(false)
  const failed = ref(false)
  // 加载、保存、AI 回写均由服务端返回新的草稿对象；本地图片编辑只改其字段。
  watch(draft, value => { saved.value = draftImageSignature(value); failed.value = false }, { flush: 'sync' })
  const dirty = computed(() => saved.value !== draftImageSignature(draft.value))
  const status = computed(() => saving.value ? '正在保存…' : failed.value ? '保存失败，请重试' : dirty.value ? '图片设置尚未保存' : '图片设置已保存')
  async function save() {
    if (saving.value) return false
    saving.value = true
    try {
      const ok = await persist()
      failed.value = !ok
      if (ok) saved.value = draftImageSignature(draft.value)
      return ok
    } catch {
      failed.value = true
      return false
    } finally { saving.value = false }
  }
  return { dirty, status, save }
}
