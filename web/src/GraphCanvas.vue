<script setup>
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Minus, Plus, Maximize, Undo2, Redo2, Trash2, RotateCcw } from '@lucide/vue'
import WorkflowNode from './WorkflowNode.vue'
import WorkflowEdge from './WorkflowEdge.vue'
import { workflow } from './graph.js'

const props = defineProps({ config: Object, task: String, result: Object, schema: Object, editLocked: Boolean, event: Object, selected: String, layers: Object, storageKey: String, readOnly: Boolean })
const emit = defineEmits(['select', 'ready', 'edit'])
const nodes = ref([]), edges = ref([]), canvas = ref(null)
const selectedEdge = ref(null), context = ref(null), feedback = ref(''), missing = ref([])
const undoStack = ref([]), redoStack = ref([])
const pendingPort = ref(null), pointer = ref(null), portError = ref('')
let dragStart = null, ignoreClickUntil = 0
const { fitView, zoomIn, zoomOut, viewport } = useVueFlow()
let positions = {}, connections = null, updatingEdge = null, connectionAdded = false, messageTimer
const zoom = computed(() => Math.round(viewport.value.zoom * 100))
const pendingLinks = computed(() => pendingPort.value ? edges.value.filter(edge => edge.source === pendingPort.value.nodeId && edge.sourceHandle === pendingPort.value.handleId || edge.target === pendingPort.value.nodeId && edge.targetHandle === pendingPort.value.handleId) : [])
const preview = computed(() => {
  if (!pendingPort.value || !pointer.value) return ''
  const { nodeId, handleId } = pendingPort.value
  const handle = canvas.value?.querySelector(`.vue-flow__node[data-id="${nodeId}"] .vue-flow__handle[data-handleid="${handleId}"]`)
  if (!handle) return ''
  const rect = handle.getBoundingClientRect(), bounds = canvas.value.getBoundingClientRect()
  const x = rect.x + rect.width / 2 - bounds.x, y = rect.y + rect.height / 2 - bounds.y
  const end = pointer.value, bend = Math.max(45, Math.abs(end.x - x) / 2) * (port(nodeId, handleId)?.direction === 'source' ? 1 : -1)
  if (Math.hypot(end.x - x, end.y - y) < 8) return ''
  return `M ${x} ${y} C ${x + bend} ${y}, ${end.x - bend} ${end.y}, ${end.x} ${end.y}`
})
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
  return !props.readOnly
}
function cancelPort() { pendingPort.value = null; pointer.value = null; portError.value = ''; refreshNodes() }
function movePointer(event) { if (pendingPort.value) { const bounds = canvas.value.getBoundingClientRect(); pointer.value = { x: event.clientX - bounds.x, y: event.clientY - bounds.y } } }
function clickPort(nodeId, handleId, event) {
  if (performance.now() < ignoreClickUntil) return
  if (props.readOnly) { notify('Run snapshot: open the working copy to edit connections.'); return }
  const clicked = port(nodeId, handleId), pending = pendingPort.value
  if (pending) {
    if (pending.nodeId === nodeId && pending.handleId === handleId) { cancelPort(); return }
    const start = port(pending.nodeId, pending.handleId)
    const connection = start.direction === 'source'
      ? { source: pending.nodeId, sourceHandle: pending.handleId, target: nodeId, targetHandle: handleId }
      : { source: nodeId, sourceHandle: handleId, target: pending.nodeId, targetHandle: pending.handleId }
    if (clicked.direction !== start.direction && valid(connection)) { connect(connection); cancelPort(); return }
    portError.value = `Incompatible port. Choose a ${start.label} ${start.direction === 'source' ? 'input' : 'output'}.`; return
  }
  selectedEdge.value = null; context.value = null
  portError.value = ''
  pendingPort.value = { nodeId, handleId, type: clicked.label, direction: clicked.direction }
  movePointer(event); refreshNodes(); canvas.value.focus()
}
function startDrag({ nodeId, handleId, event }) {
  connectionAdded = false; context.value = null
  dragStart = { nodeId, handleId, x: event.clientX, y: event.clientY }
}
function endDrag(event) {
  const start = dragStart; dragStart = null
  if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4) return
  ignoreClickUntil = performance.now() + 250; cancelPort()
  if (connectionAdded || props.readOnly) return
  const hit = document.elementFromPoint(event.clientX, event.clientY)
  if (port(start.nodeId, start.handleId)?.direction === 'target' && !hit?.closest('.vue-flow__node')) disconnectPort(start.nodeId, start.handleId)
  else notify('Drop on a matching port, or click two ports to connect.')
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
      style: { stroke: `var(--wire-${layer})`, strokeWidth: 4 },
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
    data: { ...node.data, readOnly: props.readOnly, editLocked: props.editLocked, pendingPort: pendingPort.value },
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
watch(() => props.readOnly, cancelPort)

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
  const current = connections ?? defaults().edges.map(link)
  if (current.some(edge => identity(edge) === identity(connection))) { notify('These ports are already connected.'); return }
  edit([...current.filter(edge => edge.target !== connection.target || edge.targetHandle !== connection.targetHandle), connection], 'Connection added · saved locally')
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
function paneClick() { cancelPort(); selectedEdge.value = null; context.value = null; renderEdges(); canvas.value.focus() }
function restoreConnections() { edit(defaults().edges.map(link), 'Default connections restored') }
function reset() {
  positions = {}; nodes.value = defaults().nodes.map(node => ({ ...node, selected: props.selected === node.id, data: { ...node.data, readOnly: props.readOnly, editLocked: props.editLocked } }))
  save(); requestAnimationFrame(() => fitView({ padding: .17 }))
}
function keyboard(event) {
  if (event.target.closest('input,textarea,select,button,[contenteditable]')) return
  if (event.key === 'Escape') { cancelPort(); context.value = null; selectedEdge.value = null; renderEdges(); return }
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
  <div ref="canvas" class="graph-canvas" tabindex="0" aria-label="Workflow canvas" @keydown.capture="keyboard" @pointermove="movePointer">
    <div class="canvas-caption"><span class="tiny-square"></span>{{ readOnly ? 'Run graph · view only' : 'Editable graph' }}</div>
    <div class="canvas-tools">
      <button title="Undo connection change (Ctrl Z)" aria-label="Undo connection change" :disabled="readOnly || !undoStack.length" @click="undo"><Undo2 :size="14" /></button>
      <button title="Redo connection change (Ctrl Shift Z)" aria-label="Redo connection change" :disabled="readOnly || !redoStack.length" @click="redo"><Redo2 :size="14" /></button>
      <button title="Zoom out" aria-label="Zoom out" @click="zoomOut()"><Minus :size="14" /></button><span>{{ zoom }}%</span><button title="Zoom in" aria-label="Zoom in" @click="zoomIn()"><Plus :size="14" /></button><button title="Fit graph (F)" aria-label="Fit graph" @click="fitView({ padding: .17 })"><Maximize :size="14" /></button>
    </div>
    <VueFlow v-model:nodes="nodes" v-model:edges="edges" :nodes-draggable="true" :nodes-connectable="!readOnly" :edges-updatable="!readOnly" :is-valid-connection="valid" :connect-on-click="false" :edge-updater-radius="14" :node-drag-threshold="3" :delete-key-code="null" :min-zoom=".3" :max-zoom="1.6" :fit-view-on-init="true" :fit-view-params="{ padding: .17 }" :zoom-on-double-click="false" @node-click="selectNode"  @node-drag-stop="remember" @edge-click="selectConnection" @edge-context-menu="edgeMenu" @pane-click="paneClick" @connect="connect" @connect-start="startDrag" @connect-end="endDrag" @edge-update-start="updatingEdge = $event.edge.id; connectionAdded = false" @edge-update="updateConnection" @edge-update-end="updatingEdge = null">
      <template #node-workbench="nodeProps"><WorkflowNode v-bind="nodeProps" @disconnect-port="disconnectPort(nodeProps.id, $event); cancelPort()" @port-click="(handleId, event) => clickPort(nodeProps.id, handleId, event)" @edit="(path, value) => emit('edit', path, value)" @inspect="emit('select', nodeProps.id, true)" /></template>
      <template #edge-outlined="edgeProps"><WorkflowEdge v-bind="edgeProps" :zoom="viewport.zoom" /></template>
      <template #connection-line="lineProps"><WorkflowEdge v-bind="lineProps" :zoom="viewport.zoom" preview /></template>
    </VueFlow>
    <div v-if="context" class="graph-context-menu" :style="{ left: `${context.x}px`, top: `${context.y}px` }"><button :disabled="readOnly" @click="removeConnection()"><Trash2 :size="14" /> Delete connection <kbd>Del</kbd></button><button @click="context = null">Cancel</button></div>
    <svg v-if="preview" class="port-preview"><path class="wire-outline" :d="preview" /><path class="wire-core" :d="preview" /></svg><div v-if="pendingPort" class="graph-feedback port-actions" role="status"><span>{{ portError || `${pendingPort.nodeId}.${pendingPort.handleId} · Click a ${pendingPort.type} ${pendingPort.direction === 'source' ? 'input' : 'output'}` }}</span><button v-if="pendingLinks.length" @click="disconnectPort(pendingPort.nodeId, pendingPort.handleId); cancelPort()">Disconnect port</button><button @click="cancelPort">Cancel · Esc</button></div><div v-else-if="selectedEdge" class="connection-selection"><span>Connection selected</span><button :disabled="readOnly" @click="removeConnection()"><Trash2 :size="13" /> Disconnect</button><small v-if="!readOnly">Drag either end to reconnect</small></div>
    <div v-else-if="feedback" class="graph-feedback" role="status">{{ feedback }}</div>
    <div v-else-if="missing.length && !readOnly" class="graph-feedback graph-incomplete"><span>Connect {{ missing.join(', ') }} before running.</span><button @click="restoreConnections"><RotateCcw :size="12" /> Restore</button></div>
    <div class="canvas-legend"><span><i class="config"></i> Construction inputs</span><span><i class="action"></i> Run result</span></div>
    <div class="graph-gesture-hint">Drag nodes · Click or drag ports to connect · Select wire + Delete · Use ⋯ for properties</div>
  </div>
</template>
