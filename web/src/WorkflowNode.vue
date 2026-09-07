<script setup>
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object, selected: Boolean })
const sides = { left: Position.Left, right: Position.Right, top: Position.Top, bottom: Position.Bottom }
</script>
<template>
  <article class="workflow-node" :class="{ selected, executing: data.active }">
    <header><span class="node-square"></span><strong>{{ data.title }}</strong><span v-if="data.active" class="node-live">●</span></header>
    <div class="node-subtitle">{{ data.subtitle }}</div>
    <div class="node-fields"><div v-for="([label, value], index) in data.rows" :key="index" class="node-field"><span>{{ label }}</span><b :title="String(value)">{{ value }}</b></div></div>
    <Handle v-for="port in data.ports" :id="port.id" :key="port.id" :type="port.direction" :position="sides[port.side]" :connectable="false" :title="`${port.direction}: ${port.label}`" :style="['left','right'].includes(port.side) ? { top: `${port.top}%` } : { left: `${port.top}%` }" />
  </article>
</template>
