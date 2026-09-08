<script setup>
import { computed } from 'vue'
import { BaseEdge, getBezierPath } from '@vue-flow/core'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  id: String, sourceX: Number, sourceY: Number, targetX: Number, targetY: Number,
  sourcePosition: String, targetPosition: String, markerStart: String, markerEnd: String,
  selected: Boolean, data: Object, preview: Boolean,
})
const path = computed(() => getBezierPath(props)[0])
const color = computed(() => props.preview ? '#ed97cd' : props.selected ? '#f4a3d3' : props.data?.layer === 'action' ? '#cf559e' : '#a6a292')
</script>

<template>
  <path class="wire-outline" :d="path" />
  <BaseEdge :id="id" :path="path" :marker-start="markerStart" :marker-end="markerEnd"
    :interaction-width="preview ? 0 : 24" class="wire-core" :style="{ stroke: color }" />
</template>
