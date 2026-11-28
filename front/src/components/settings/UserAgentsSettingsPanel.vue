<script setup lang="ts">
import { computed, onMounted, ref, useId } from 'vue'
import PageSaveButton from '@/components/layout/PageSaveButton.vue'
import { usePageSaveAction, type PageSaveAction } from '@/composables/usePageSaveAction'
import { fetchUserAgents, saveUserAgents } from '@/api/userAgents'

const formId = useId()
const formEl = ref<HTMLFormElement | null>(null)
const content = ref('')
const savedContent = ref('')
const revision = ref('')
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const bytes = computed(() => new TextEncoder().encode(content.value).length)
const valid = computed(() => bytes.value <= 32 * 1024)
const saveAction = computed<PageSaveAction | null>(() => (
  revision.value
    ? {
        label: saving.value ? '保存中…' : '保存自定义指令',
        disabled: saving.value || loading.value || !valid.value || content.value === savedContent.value,
        form: formId,
        submitForm: () => formEl.value?.requestSubmit(),
      }
    : null
))
const { hasHost } = usePageSaveAction(saveAction)

async function load() {
  if (saving.value) return
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const document = await fetchUserAgents()
    content.value = savedContent.value = document.content
    revision.value = document.revision
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '自定义指令读取失败' }
  finally { loading.value = false }
}

async function save() {
  if (saving.value || loading.value || !revision.value || !valid.value || content.value === savedContent.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const document = await saveUserAgents(content.value, revision.value)
    content.value = savedContent.value = document.content
    revision.value = document.revision
    notice.value = '已保存，下次 AI 运行时生效。'
  } catch (exc) { error.value = exc instanceof Error ? exc.message : '自定义指令保存失败' }
  finally { saving.value = false }
}

onMounted(load)
</script>

<template>
  <form :id="formId" ref="formEl" class="space-y-4" @submit.prevent="save">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="font-semibold">AI 自定义指令</h3>
        <p id="user-agents-help" class="muted mt-2 text-sm">填写希望 AI 长期遵循的交流偏好与业务习惯，仅对你的账号生效，升级时保留。请在注释外填写实际要求，注释文字不会发送给 AI；自定义指令仍须遵守系统规则。</p>
      </div>
      <div v-if="revision" class="flex flex-wrap items-center gap-2">
        <button v-if="error" type="button" class="btn btn-outline" @click="load">重新读取（放弃未保存修改）</button>
        <PageSaveButton v-if="!hasHost && saveAction" :action="saveAction" />
      </div>
    </div>
    <p v-if="loading" role="status">正在读取自定义指令…</p>
    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
    <p v-if="notice" role="status" class="text-sm text-green-700 dark:text-green-400">{{ notice }}</p>
    <button v-if="!revision && !loading" type="button" class="btn btn-outline" @click="load">重新读取</button>
    <fieldset v-if="revision" :disabled="loading || saving" class="space-y-3">
      <label for="user-agents-content" class="block text-sm font-medium">userAgents.md</label>
      <textarea id="user-agents-content" v-model="content" rows="18" class="input block w-full resize-y font-mono text-sm leading-6" spellcheck="false" aria-describedby="user-agents-help user-agents-size" @input="notice = ''" />
      <p id="user-agents-size" class="text-xs" :class="valid ? 'text-accent-500 dark:text-accent-400' : 'text-red-600 dark:text-red-400'">{{ bytes }} / 32768 字节<span v-if="!valid">，内容超过上限，请精简后保存。</span></p>
    </fieldset>
  </form>
</template>
