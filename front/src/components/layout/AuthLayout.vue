<script setup lang="ts">
import logoCartUrl from '@/assets/logo-cart.svg'
import { APP_DESCRIPTION, APP_SHORT_NAME } from '@/constants/branding'
</script>

<template>
  <div class="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
    <div class="absolute inset-0 bg-gradient-to-br from-accent-50 via-primary-50/30 to-accent-100 dark:from-dark-950 dark:via-dark-900 dark:to-dark-950" />
    <div class="relative z-10 w-full max-w-md">
      <div class="mb-8 text-center">
        <div class="mx-auto flex size-16 items-center justify-center rounded-3xl bg-white p-2.5 shadow-glass ring-1 ring-primary-100 dark:bg-dark-800 dark:ring-white/15">
          <img :src="logoCartUrl" alt="Champion ERP logo" class="size-full" />
        </div>
        <h1 class="mt-4 text-2xl font-black text-accent-950 dark:text-white">{{ APP_SHORT_NAME }}</h1>
        <p class="mt-2 text-sm text-accent-500 dark:text-accent-300">{{ APP_DESCRIPTION }}</p>
      </div>
      <div class="rounded-2xl border border-white/60 bg-white/80 p-8 shadow-glass backdrop-blur dark:border-dark-700 dark:bg-dark-900/80">
        <slot />
      </div>
      <div class="mt-6 text-center text-sm text-accent-500 dark:text-accent-300">
        <slot name="footer" />
      </div>
    </div>
  </div>
</template>
