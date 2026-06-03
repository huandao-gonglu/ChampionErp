<script setup lang="ts">
import { RouterView } from 'vue-router'
import NavigationProgress from '@/components/common/NavigationProgress.vue'
import Toast from '@/components/common/Toast.vue'
</script>

<template>
  <NavigationProgress />
  <RouterView />
  <Toast />
</template>
