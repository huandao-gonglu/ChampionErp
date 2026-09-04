<script setup lang="ts">
import { RouterView } from 'vue-router'
import AiWorkFloatingButton from '@/components/common/AiWorkFloatingButton.vue'
import NavigationProgress from '@/components/common/NavigationProgress.vue'
import Toast from '@/components/common/Toast.vue'
</script>

<template>
  <NavigationProgress />
  <RouterView v-slot="{ Component, route }">
    <KeepAlive>
      <component
        :is="Component"
        v-if="route.meta.keepAlive"
        :key="String(route.name)"
      />
    </KeepAlive>
    <component
      :is="Component"
      v-if="!route.meta.keepAlive"
      :key="String(route.name)"
    />
  </RouterView>
  <AiWorkFloatingButton />
  <Toast />
</template>
