import { computed, ref } from 'vue'
import type { FileUIPart } from 'ai'
import { modelOperationReason } from '@/utils/desktop'

const imageTypes = ['image/jpeg', 'image/png', 'image/webp']
export const chatImageAccept = imageTypes.join(',')
const mib = 1024 * 1024

function readImage(file: File): Promise<FileUIPart> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('图片读取失败，请重新选择。'))
    reader.onload = () => resolve({ type: 'file', mediaType: file.type, filename: file.name, url: String(reader.result) })
    reader.readAsDataURL(file)
  })
}

export function useChatImages() {
  const attachments = ref<{ part: FileUIPart; size: number }[]>([])
  const reading = ref(false)
  const error = ref('')
  const blocked = computed(() => modelOperationReason('global.chat.images'))
  let revision = 0

  function clear() {
    revision++
    attachments.value = []
    reading.value = false
    error.value = ''
  }

  async function add(files: File[]) {
    if (!files.length || reading.value) return
    const size = [...attachments.value, ...files].reduce((sum, item) => sum + item.size, 0)
    error.value = blocked.value
    if (!error.value) {
      if (files.some(file => !imageTypes.includes(file.type))) error.value = '请选择 JPEG、PNG 或 WebP 图片。'
      else if (attachments.value.length + files.length > 4) error.value = '每条消息最多可附 4 张图片。'
      else if (files.some(file => !file.size || file.size > 8 * mib)) error.value = '单张图片不能为空或超过 8 MiB。'
      else if (size > 20 * mib) error.value = '每条消息的图片合计不能超过 20 MiB。'
    }
    if (error.value) return
    const current = revision
    reading.value = true
    try {
      const images = await Promise.all(files.map(async file => ({ part: await readImage(file), size: file.size })))
      if (current === revision) attachments.value.push(...images)
    } catch {
      if (current === revision) error.value = '图片读取失败，请重新选择。'
    } finally {
      if (current === revision) reading.value = false
    }
  }
  return { attachments, reading, error, blocked, add, clear }
}
