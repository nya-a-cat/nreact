<script setup>
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Minus, Plus, Maximize, Move } from '@lucide/vue'
import WorkflowNode from './WorkflowNode.vue'
import { workflow } from './graph.js'
const props = defineProps({ config: Object, task: String, answer: String, event: Object, selected: String, layers: Object, storageKey: String })
const emit = defineEmits(['select'])
const nodes = ref([]), edges = ref([])
const canvas = ref(null)
const { fitView, zoomIn, zoomOut, viewport } = useVueFlow()
let positions = {}
watch(() => props.storageKey, key => { try { positions = JSON.parse(localStorage.getItem(`nreact-layout:${key}`) || '{}') } catch { positions = {} } }, { immediate: true })
watch(() => [props.config, props.task, props.answer, props.event, props.selected, props.layers, props.storageKey], () => {
  const graph = workflow(props.config, props.task, props.answer, props.event)
  nodes.value = graph.nodes.map(node => ({ ...node, position: positions[node.id] || node.position, selected: props.selected === node.id }))
  edges.value = graph.edges.filter(edge => props.layers[edge.data.layer])
}, { deep: true, immediate: true })
function remember({ node }) {
  positions[node.id] = { ...node.position }
  try { localStorage.setItem(`nreact-layout:${props.storageKey}`, JSON.stringify(positions)) } catch { /* layout persistence is optional */ }
}
function reset() { positions = {}; const graph = workflow(props.config, props.task, props.answer, props.event); nodes.value = graph.nodes.map(node => ({ ...node, selected: props.selected === node.id })); try { localStorage.removeItem(`nreact-layout:${props.storageKey}`) } catch {} ; requestAnimationFrame(() => fitView({ padding: .12 })) }
defineExpose({ reset, fit: () => fitView({ padding: .12 }) })
const zoom = computed(() => Math.round(viewport.value.zoom * 100))
let observer, resizeTimer
onMounted(() => {
  observer = new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => fitView({ padding: .17 }), 120) })
  observer.observe(canvas.value)
})
onUnmounted(() => { observer?.disconnect(); clearTimeout(resizeTimer) })
</script>
<template>
  <div ref="canvas" class="graph-canvas">
    <div class="canvas-caption"><span class="tiny-square"></span> Agent configuration <span class="subtle">/ {{ nodes.length }} components</span></div>
    <div class="canvas-tools"><button title="Zoom out" aria-label="Zoom out" @click="zoomOut()"><Minus :size="14" /></button><span>{{ zoom }}%</span><button title="Zoom in" aria-label="Zoom in" @click="zoomIn()"><Plus :size="14" /></button><button title="Fit graph (F)" aria-label="Fit graph" @click="fitView({ padding: .12 })"><Maximize :size="14" /></button></div>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" :nodes-connectable="false" :edges-updatable="false" :delete-key-code="null" :min-zoom=".3" :max-zoom="1.6" :fit-view-on-init="true" :fit-view-params="{ padding: .12 }" :zoom-on-double-click="false" @node-click="emit('select', $event.node.id)" @node-drag-stop="remember">
      <template #node-workbench="nodeProps"><WorkflowNode v-bind="nodeProps" /></template>
    </VueFlow>
    <div class="canvas-legend"><span><i class="config"></i> Configuration</span><span><i class="action"></i> Action</span><span><i class="observation"></i> Observation</span></div>
    <div class="canvas-hint"><Move :size="12" /> Drag to pan · Scroll to zoom</div>
  </div>
</template>
