<script setup>
import { computed } from 'vue'
import { BaseEdge, getBezierPath } from '@vue-flow/core'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  id: String, sourceX: Number, sourceY: Number, targetX: Number, targetY: Number,
  sourcePosition: String, targetPosition: String, markerStart: String, markerEnd: String,
  selected: Boolean, data: Object, preview: Boolean,
  zoom: { type: Number, default: 1 },
})
const path = computed(() => getBezierPath(props)[0])
const color = computed(() => `var(--wire-${props.preview ? 'preview' : props.selected ? 'selected' : props.data?.layer === 'action' ? 'action' : 'config'})`)
</script>

<template>
  <path class="wire-outline" :d="path" :style="{ strokeWidth: 6 / zoom }" />
  <BaseEdge :id="id" :path="path" :marker-start="markerStart" :marker-end="markerEnd"
    :interaction-width="preview ? 0 : 24" class="wire-core" :style="{ stroke: color, strokeWidth: 4 / zoom }" />
</template>
