<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { version } from '../../../package.json'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import OpenSourceNotices from './OpenSourceNotices.vue'
import { companyName, supportEmail } from '@/legal/legal-info'
import { agreementTitle, agreementVersion, agreementIntroduction, agreementSections } from '@/legal/user-agreement'
import { privacyTitle, privacyVersion, privacyIntroduction, privacySections } from '@/legal/privacy-policy'

const { t } = useI18n()
const document = ref<'licenses' | 'agreement' | 'privacy' | null>(null)
const legalDocument = computed(() => {
  if (document.value === 'agreement') return { title: agreementTitle, version: agreementVersion, introduction: agreementIntroduction, sections: agreementSections }
  if (document.value === 'privacy') return { title: privacyTitle, version: privacyVersion, introduction: privacyIntroduction, sections: privacySections }
  return null
})
</script>

<template>
  <div class="space-y-6">
    <section class="rounded-xl border border-accent-200 bg-accent-50 p-6 dark:border-dark-700 dark:bg-dark-950/70">
      <p class="text-xs font-semibold uppercase tracking-widest text-accent-500">{{ t('settings.about') }}</p>
      <div class="mt-3 flex flex-wrap items-center gap-3">
        <h3 class="text-2xl font-bold tracking-tight">Champion ERP</h3>
        <span class="rounded-full border border-accent-200 bg-white px-3 py-1 text-xs font-medium tabular-nums dark:border-dark-700 dark:bg-dark-900" data-testid="app-version">{{ t('settings.version') }} {{ version }}</span>
      </div>
      <p class="mt-3 text-sm text-accent-600 dark:text-accent-300">{{ companyName }}</p>
      <a class="mt-2 inline-block break-all text-sm text-primary-700 underline underline-offset-4 dark:text-primary-300" :href="`mailto:${supportEmail}`">{{ supportEmail }}</a>
    </section>

    <div class="divide-y divide-accent-200 overflow-hidden rounded-xl border border-accent-200 dark:divide-dark-700 dark:border-dark-700">
      <div class="flex flex-wrap items-center justify-between gap-4 p-5">
        <div><h4 class="font-semibold">{{ t('settings.openSource') }}</h4><p class="muted mt-1 text-sm">{{ t('settings.openSourceSummary') }}</p></div>
        <button type="button" class="btn btn-outline shrink-0" data-testid="view-licenses" aria-haspopup="dialog" @click="document = 'licenses'">{{ t('settings.view') }}<span class="sr-only">{{ t('settings.openSource') }}</span></button>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-4 p-5">
        <div><h4 class="font-semibold">{{ t('settings.userAgreement') }}</h4><p class="muted mt-1 text-sm">{{ t('settings.userAgreementSummary') }}</p></div>
        <button type="button" class="btn btn-outline shrink-0" data-testid="view-agreement" aria-haspopup="dialog" @click="document = 'agreement'">{{ t('settings.view') }}<span class="sr-only">{{ t('settings.userAgreement') }}</span></button>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-4 p-5">
        <div><h4 class="font-semibold">{{ t('settings.privacyPolicy') }}</h4><p class="muted mt-1 text-sm">{{ t('settings.privacyPolicySummary') }}</p></div>
        <button type="button" class="btn btn-outline shrink-0" data-testid="view-privacy" aria-haspopup="dialog" @click="document = 'privacy'">{{ t('settings.view') }}<span class="sr-only">{{ t('settings.privacyPolicy') }}</span></button>
      </div>
    </div>

    <WorkspaceDialog v-if="document" :open="true" :title="legalDocument?.title ?? 'Open Source Licenses'" width="72rem" @close="document = null">
      <OpenSourceNotices v-if="document === 'licenses'" />
      <article v-else-if="legalDocument" lang="zh-CN" class="mx-auto max-w-4xl space-y-7 text-sm leading-7">
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
