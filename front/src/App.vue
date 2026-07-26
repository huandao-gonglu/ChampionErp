<script setup lang="ts">
import { RouterView } from 'vue-router'
import AiWorkFloatingButton from '@/components/common/AiWorkFloatingButton.vue'
import NavigationProgress from '@/components/common/NavigationProgress.vue'
import Toast from '@/components/common/Toast.vue'
</script>

<template>
  <NavigationProgress />
  <RouterView />
  <AiWorkFloatingButton />
  <Toast />
</template>
