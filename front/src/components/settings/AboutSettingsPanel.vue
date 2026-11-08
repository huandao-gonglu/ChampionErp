<script setup lang="ts">
import { computed, ref } from 'vue'
import { APP_NAME } from '@/constants/branding'
import { version } from '../../../package.json'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { companyName, supportEmail } from '@/legal/legal-info'
import { agreementTitle, agreementVersion, agreementIntroduction, agreementSections } from '@/legal/user-agreement'
import { privacyTitle, privacyVersion, privacyIntroduction, privacySections } from '@/legal/privacy-policy'

const document = ref<'agreement' | 'privacy' | null>(null)
const desktop = window.championDesktop
const licensesUrl = `${import.meta.env.BASE_URL}licenses/THIRD-PARTY-NOTICES.txt`
const openingLicenses = ref(false)
const licensesError = ref('')
async function openLicenses() {
  if (!desktop || openingLicenses.value) return
  licensesError.value = ''
  openingLicenses.value = true
  try {
    await desktop.openLicenses()
  } catch {
    licensesError.value = '无法打开许可证文件夹，请重试。'
  } finally {
    openingLicenses.value = false
  }
}
const legalDocument = computed(() => {
  if (document.value === 'agreement') return { title: agreementTitle, version: agreementVersion, introduction: agreementIntroduction, sections: agreementSections }
  if (document.value === 'privacy') return { title: privacyTitle, version: privacyVersion, introduction: privacyIntroduction, sections: privacySections }
  return null
})
</script>

<template>
  <div class="space-y-6">
    <section class="rounded-xl border border-accent-200 bg-accent-50 p-6 dark:border-dark-700 dark:bg-dark-950/70">
      <p class="text-xs font-semibold uppercase tracking-widest text-accent-500">关于</p>
      <div class="mt-3 flex flex-wrap items-center gap-3">
        <h3 class="text-2xl font-bold tracking-tight">{{ APP_NAME }}</h3>
        <span class="rounded-full border border-accent-200 bg-white px-3 py-1 text-xs font-medium tabular-nums dark:border-dark-700 dark:bg-dark-900" data-testid="app-version">版本 {{ version }}</span>
      </div>
      <p class="mt-3 text-sm text-accent-600 dark:text-accent-300">{{ companyName }}</p>
      <a class="mt-2 inline-block break-all text-sm text-primary-700 underline underline-offset-4 dark:text-primary-300" :href="`mailto:${supportEmail}`">{{ supportEmail }}</a>
    </section>

    <div class="divide-y divide-accent-200 overflow-hidden rounded-xl border border-accent-200 dark:divide-dark-700 dark:border-dark-700">
      <div class="flex flex-wrap items-center justify-between gap-4 p-5">
        <div>
          <h4 class="font-semibold">开源许可证</h4>
          <p class="muted mt-1 text-sm">查看第三方组件、版权声明与许可证原文</p>
          <p v-if="licensesError" role="alert" class="mt-1 text-sm text-red-600 dark:text-red-400">{{ licensesError }}</p>
        </div>
        <button v-if="desktop" type="button" class="btn btn-outline shrink-0" data-testid="view-licenses" :disabled="openingLicenses" @click="openLicenses">查看<span class="sr-only">开源许可证</span></button>
        <a v-else class="btn btn-outline shrink-0" data-testid="view-licenses" :href="licensesUrl" download="THIRD-PARTY-NOTICES.txt">下载声明</a>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-4 p-5">
        <div><h4 class="font-semibold">软件许可与服务协议</h4><p class="muted mt-1 text-sm">自行部署、免费使用、可选收费服务与 AI 使用责任</p></div>
        <button type="button" class="btn btn-outline shrink-0" data-testid="view-agreement" aria-haspopup="dialog" @click="document = 'agreement'">查看<span class="sr-only">软件许可与服务协议</span></button>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-4 p-5">
        <div><h4 class="font-semibold">隐私政策</h4><p class="muted mt-1 text-sm">硬件信息的处理范围、保存期限与您的权利</p></div>
        <button type="button" class="btn btn-outline shrink-0" data-testid="view-privacy" aria-haspopup="dialog" @click="document = 'privacy'">查看<span class="sr-only">隐私政策</span></button>
      </div>
    </div>

    <WorkspaceDialog v-if="legalDocument" :open="true" :title="legalDocument.title" width="72rem" @close="document = null">
      <article lang="zh-CN" class="mx-auto max-w-4xl space-y-7 text-sm leading-7">
        <div>
          <p class="text-accent-500 dark:text-accent-400">版本日期：{{ legalDocument.version }}</p>
          <p class="mt-4 font-semibold">{{ legalDocument.introduction }}</p>
        </div>
        <section v-for="item in legalDocument.sections" :key="item.title">
          <h4 class="mb-3 text-base font-bold">{{ item.title }}</h4>
          <div class="space-y-3" :class="{ 'font-semibold': 'important' in item && item.important }">
            <p v-for="paragraph in item.paragraphs" :key="paragraph">{{ paragraph }}</p>
          </div>
        </section>
        <p class="border-t border-accent-200 pt-5 dark:border-dark-700">联系邮箱：<a class="break-all text-primary-700 underline dark:text-primary-300" :href="`mailto:${supportEmail}`">{{ supportEmail }}</a></p>
      </article>
    </WorkspaceDialog>
  </div>
</template>
