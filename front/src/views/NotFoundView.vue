<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-accent-100 p-6 dark:bg-dark-950">
    <section class="card max-w-lg text-center">
      <p class="text-sm font-semibold uppercase tracking-[0.2em] text-primary-600">404</p>
      <h1 class="mt-3 text-3xl font-black text-accent-950 dark:text-white">页面不存在</h1>
      <p class="mt-3 text-sm text-accent-500 dark:text-accent-300">该页面不存在或已经迁移到新版 Vue 工作台。</p>
      <RouterLink to="/" class="btn btn-primary mt-6">返回工作台</RouterLink>
    </section>
  </main>
</template>
