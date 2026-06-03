<script setup lang="ts">
import { computed } from 'vue'
import { statusBadgeClass, workflowStatusLabel } from '@/utils/status'

const props = defineProps<{
  status: string
  label?: string
}>()

const classes = computed(() => statusBadgeClass(props.status))
const text = computed(() => props.label || workflowStatusLabel(props.status))
</script>

<template>
  <span :class="classes">{{ text }}</span>
</template>
