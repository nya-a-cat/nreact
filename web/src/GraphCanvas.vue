<script setup>
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Minus, Plus, Maximize, Undo2, Redo2, Trash2, RotateCcw } from '@lucide/vue'
import WorkflowNode from './WorkflowNode.vue'
import { workflow } from './graph.js'

const props = defineProps({ config: Object, task: String, result: Object, schema: Object, editLocked: Boolean, event: Object, selected: String, layers: Object, storageKey: String, readOnly: Boolean })
const emit = defineEmits(['select', 'ready', 'edit'])
const nodes = ref([]), edges = ref([]), canvas = ref(null)
const selectedEdge = ref(null), context = ref(null), feedback = ref(''), missing = ref([])
const undoStack = ref([]), redoStack = ref([])
const { fitView, zoomIn, zoomOut, viewport } = useVueFlow()
let positions = {}, connections = null, updatingEdge = null, connectionAdded = false, messageTimer
const zoom = computed(() => Math.round(viewport.value.zoom * 100))
const defaults = () => workflow(props.config, props.task, props.result, props.event, props.schema)
const link = edge => ({ source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle, targetHandle: edge.targetHandle })
const identity = edge => `${edge.source}:${edge.sourceHandle}->${edge.target}:${edge.targetHandle}`
const notify = text => { feedback.value = text; clearTimeout(messageTimer); messageTimer = setTimeout(() => { feedback.value = '' }, 4500) }

function port(nodeId, handleId) { return nodes.value.find(node => node.id === nodeId)?.data.ports.find(item => item.id === handleId) }
function valid(connection) {
  if (connection.source === connection.target) return false
  const source = port(connection.source, connection.sourceHandle), target = port(connection.target, connection.targetHandle)
  if (source?.direction !== 'source' || target?.direction !== 'target' || source.label !== target.label) return false
  // Vue Flow also validates already-created edges during setEdges. Their own
  // occupied input must not invalidate them when a different wire is edited.
  if (connection.id) return true
  return !props.readOnly && !edges.value.some(edge => edge.id !== updatingEdge && edge.target === connection.target && edge.targetHandle === connection.targetHandle)
}
function save() {
  try {
    localStorage.setItem(`nreact-graph:${props.storageKey}`, JSON.stringify({ version: 2, positions, connections }))
    localStorage.setItem(`nreact-layout:${props.storageKey}`, JSON.stringify(positions))
  } catch { notify('Could not save the diagram in this browser. Export the graph to keep a copy.') }
}
function renderEdges() {
  const standard = defaults().edges
  const current = props.readOnly ? standard.map(link) : connections ?? standard.map(link)
  edges.value = current.map(connection => {
    const base = standard.find(edge => identity(edge) === identity(connection))
    const type = port(connection.source, connection.sourceHandle)?.label
    const layer = type === 'Result' ? 'action' : 'config'
    return { ...(base || standard[0]), ...connection, id: identity(connection), label: undefined,
      data: { layer }, hidden: !props.layers[layer], selected: selectedEdge.value === identity(connection),
      updatable: !props.readOnly, selectable: true, interactionWidth: 24,
      style: { stroke: layer === 'action' ? '#cf559e' : '#a6a292', strokeWidth: 2},
    }
  })
  missing.value = standard.filter(edge => !current.some(connection => identity(connection) === identity(edge))).map(edge => `${edge.target}.${edge.targetHandle}`)
  emit('ready', { valid: missing.value.length === 0, missing: [...missing.value], connections: current.map(link) })
}
function refreshNodes() {
  const existing = new Map(nodes.value.map(node => [node.id, node]))
  nodes.value = defaults().nodes.map(node => ({ ...node,
    position: existing.get(node.id)?.position || positions[node.id] || node.position,
    draggable: true, connectable: !props.readOnly,
    selected: !selectedEdge.value && props.selected === node.id,
    data: { ...node.data, readOnly: props.readOnly, editLocked: props.editLocked },
  }))
  renderEdges()
}
watch(() => props.storageKey, key => {
  positions = {}; connections = null; nodes.value = []; undoStack.value = []; redoStack.value = []
  try {
    const saved = JSON.parse(localStorage.getItem(`nreact-graph:${key}`) || 'null')
    positions = saved?.version === 2 ? saved.positions || {} : {}
    connections = saved?.version === 2 && Array.isArray(saved?.connections) ? saved.connections : null
  } catch { notify('Saved diagram could not be read. Using the initial layout.') }
  refreshNodes()
}, { immediate: true })
watch(() => [props.config, props.task, props.result, props.schema, props.editLocked, props.event, props.selected, props.readOnly], refreshNodes, { deep: true })
watch(() => props.layers, renderEdges, { deep: true })

function edit(next, message) {
  if (props.readOnly) { notify('Open the working copy to edit connections.'); return }
  undoStack.value.push((connections ?? defaults().edges.map(link)).map(link))
  if (undoStack.value.length > 50) undoStack.value.shift()
  redoStack.value = []; connections = next.map(link); selectedEdge.value = null; context.value = null
  renderEdges(); save(); notify(message)
}
function connect(connection) {
  if (!valid(connection)) return
  connectionAdded = true
  edit([...(connections ?? defaults().edges.map(link)), connection], 'Connection added · saved locally')
}
function updateConnection({ edge, connection }) {
  if (!valid(connection)) return
  connectionAdded = true
  edit((connections ?? defaults().edges.map(link)).map(item => identity(item) === edge.id ? link(connection) : item), 'Connection updated · saved locally')
}
function removeConnection(id = selectedEdge.value) {
  if (!id) return
  edit((connections ?? defaults().edges.map(link)).filter(edge => identity(edge) !== id), 'Connection removed · saved locally')
}
function disconnectPort(nodeId, handleId) {
  const current = connections ?? defaults().edges.map(link)
  const next = current.filter(edge => !((edge.source === nodeId && edge.sourceHandle === handleId) || (edge.target === nodeId && edge.targetHandle === handleId)))
  if (next.length !== current.length) edit(next, 'Port disconnected · saved locally')
}
function undo() {
  if (props.readOnly || !undoStack.value.length) return
  redoStack.value.push((connections ?? defaults().edges.map(link)).map(link)); connections = undoStack.value.pop()
  selectedEdge.value = null; renderEdges(); save(); notify('Connection change undone')
}
function redo() {
  if (props.readOnly || !redoStack.value.length) return
  undoStack.value.push((connections ?? defaults().edges.map(link)).map(link)); connections = redoStack.value.pop()
  selectedEdge.value = null; renderEdges(); save(); notify('Connection change restored')
}
function remember({ node }) { positions[node.id] = { ...node.position }; save() }
function selectNode({ node }) { selectedEdge.value = null; context.value = null; emit('select', node.id, false) }
function selectConnection({ edge }) { selectedEdge.value = edge.id; context.value = null; nodes.value.forEach(node => { node.selected = false }); renderEdges(); canvas.value.focus() }
function edgeMenu({ edge, event }) {
  event.preventDefault(); selectConnection({ edge })
  const bounds = canvas.value.getBoundingClientRect()
  context.value = { x: Math.min(event.clientX - bounds.left, bounds.width - 220), y: Math.min(event.clientY - bounds.top, bounds.height - 100) }
}
function paneClick() { selectedEdge.value = null; context.value = null; renderEdges(); canvas.value.focus() }
function restoreConnections() { edit(defaults().edges.map(link), 'Default connections restored') }
function reset() {
  positions = {}; nodes.value = defaults().nodes.map(node => ({ ...node, selected: props.selected === node.id, data: { ...node.data, readOnly: props.readOnly, editLocked: props.editLocked } }))
  save(); requestAnimationFrame(() => fitView({ padding: .17 }))
}
function keyboard(event) {
  if (event.target.closest('input,textarea,select,button,[contenteditable]')) return
  if (event.key === 'Escape') { context.value = null; selectedEdge.value = null; renderEdges(); return }
  if ((event.ctrlKey || event.metaKey) && ['z', 'y'].includes(event.key.toLowerCase())) {
    event.preventDefault(); event.stopPropagation()
    event.key.toLowerCase() === 'y' || event.shiftKey ? redo() : undo(); return
  }
  if (['Delete', 'Backspace'].includes(event.key) && selectedEdge.value) {
    event.preventDefault(); event.stopPropagation(); removeConnection(); return
  }
  const element = event.target.closest('.vue-flow__node')
  const node = nodes.value.find(item => item.id === element?.dataset.id)
  const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
  if (!node || (!['Enter', ' '].includes(event.key) && !directions[event.key])) return
  event.preventDefault(); event.stopPropagation()
  if (directions[event.key]) { const [x, y] = directions[event.key], amount = event.shiftKey ? 50 : 10; node.position = { x: node.position.x + x * amount, y: node.position.y + y * amount }; remember({ node }) }
  else emit('select', node.id, true)
}
defineExpose({ reset, restoreConnections, fit: () => fitView({ padding: .17 }), document: () => ({ version: 2, positions, connections: (connections ?? defaults().edges.map(link)).map(link) }) })
let observer, resizeTimer
onMounted(() => { observer = new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => fitView({ padding: .17 }), 120) }); observer.observe(canvas.value) })
onUnmounted(() => { observer?.disconnect(); clearTimeout(resizeTimer); clearTimeout(messageTimer) })
</script>

<template>
  <div ref="canvas" class="graph-canvas" tabindex="0" aria-label="Workflow canvas" @keydown.capture="keyboard">
    <div class="canvas-caption"><span class="tiny-square"></span>{{ readOnly ? 'Run graph · view only' : 'Editable graph' }}</div>
    <div class="canvas-tools">
      <button title="Undo connection change (Ctrl Z)" aria-label="Undo connection change" :disabled="readOnly || !undoStack.length" @click="undo"><Undo2 :size="14" /></button>
      <button title="Redo connection change (Ctrl Shift Z)" aria-label="Redo connection change" :disabled="readOnly || !redoStack.length" @click="redo"><Redo2 :size="14" /></button>
      <button title="Zoom out" aria-label="Zoom out" @click="zoomOut()"><Minus :size="14" /></button><span>{{ zoom }}%</span><button title="Zoom in" aria-label="Zoom in" @click="zoomIn()"><Plus :size="14" /></button><button title="Fit graph (F)" aria-label="Fit graph" @click="fitView({ padding: .17 })"><Maximize :size="14" /></button>
    </div>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" :nodes-draggable="true" :nodes-connectable="!readOnly" :edges-updatable="!readOnly" :is-valid-connection="valid" :connect-on-click="false" :edge-updater-radius="14" :node-drag-threshold="3" :delete-key-code="null" :min-zoom=".3" :max-zoom="1.6" :fit-view-on-init="true" :fit-view-params="{ padding: .17 }" :zoom-on-double-click="false" @node-click="selectNode"  @node-drag-stop="remember" @edge-click="selectConnection" @edge-context-menu="edgeMenu" @pane-click="paneClick" @connect="connect" @connect-start="connectionAdded = false; context = null" @connect-end="!connectionAdded && !readOnly && notify('Drag an output to an unused input with the same type.')" @edge-update-start="updatingEdge = $event.edge.id; connectionAdded = false" @edge-update="updateConnection" @edge-update-end="updatingEdge = null">
      <template #node-workbench="nodeProps"><WorkflowNode v-bind="nodeProps" @disconnect-port="disconnectPort(nodeProps.id, $event)" @edit="(path, value) => emit('edit', path, value)" @inspect="emit('select', nodeProps.id, true)" /></template>
    </VueFlow>
    <div v-if="context" class="graph-context-menu" :style="{ left: `${context.x}px`, top: `${context.y}px` }"><button :disabled="readOnly" @click="removeConnection()"><Trash2 :size="14" /> Delete connection <kbd>Del</kbd></button><button @click="context = null">Cancel</button></div>
    <div v-if="selectedEdge" class="connection-selection"><span>Connection selected</span><button :disabled="readOnly" @click="removeConnection()"><Trash2 :size="13" /> Disconnect</button><small v-if="!readOnly">Drag either end to reconnect</small></div>
    <div v-else-if="feedback" class="graph-feedback" role="status">{{ feedback }}</div>
    <div v-else-if="missing.length && !readOnly" class="graph-feedback graph-incomplete"><span>Connect {{ missing.join(', ') }} before running.</span><button @click="restoreConnections"><RotateCcw :size="12" /> Restore</button></div>
    <div class="canvas-legend"><span><i class="config"></i> Construction inputs</span><span><i class="action"></i> Run result</span></div>
    <div class="graph-gesture-hint">Drag nodes · Drag ports to connect · Select wire + Delete · Use ⋯ for properties</div>
  </div>
</template>
