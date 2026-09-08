<script setup>
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object, selected: Boolean })
const emit = defineEmits(['disconnect-port', 'port-click', 'edit', 'inspect'])
const change = (field, event) => emit('edit', field.path, field.kind === 'boolean' ? event.target.checked : field.kind === 'number' ? (event.target.value === '' ? '' : Number(event.target.value)) : event.target.value)
function updateTool(field, index, key, value) { emit('edit', field.path, field.value.map((tool, i) => i === index ? { ...tool, [key]: value } : tool)) }
function addTool(field) {
  const names = new Set(field.value.map(tool => tool.name.toLowerCase())); let number = 1
  while (names.has(`tool${number}`)) number++
  emit('edit', field.path, [...field.value, { name: `tool${number}`, description: '', callable: '' }])
}
</script>
<template>
  <article class="workflow-node" :class="{ selected, executing: data.active }">
    <header><span class="node-square"></span><strong>{{ data.title }}</strong><span v-if="data.active" class="node-live">●</span><button class="node-properties nodrag" title="Open properties" :aria-label="`Open ${data.title} properties`" @click.stop="emit('inspect')">⋯</button></header>
    <div class="node-subtitle">{{ data.subtitle }}</div>
    <div class="node-sockets" :style="{ height: `${data.portHeight}px` }">
      <div v-for="port in data.ports" :key="`${port.direction}-${port.id}`" class="socket-name" :class="port.side" :style="{ top: `${port.offset - 60}px` }"><span>{{ port.id }}</span><small>{{ port.type }}</small></div>
    </div>
    <fieldset class="node-widgets nodrag nowheel" :disabled="data.readOnly || data.editLocked" @pointerdown.stop @dblclick.stop>
      <template v-for="field in data.fields" :key="field.path">
        <div v-if="field.kind === 'tools'" class="node-tool-list">
          <div class="node-tool-heading"><span>custom</span><button :disabled="field.value.length >= 32" @click="addTool(field)">+ Add function</button></div>
          <div v-for="(tool, index) in field.value" :key="index" class="node-custom-tool">
            <label v-for="key in ['name', 'callable', 'description']" :key="key"><span>{{ key }}</span><input :aria-label="`custom ${index + 1} ${key}`" :value="tool[key]" @input="updateTool(field, index, key, $event.target.value)" /></label>
            <button @click="emit('edit', field.path, field.value.filter((_, i) => i !== index))">Remove</button>
          </div>
        </div>
        <label v-else class="node-widget" :class="{ multiline: ['textarea', 'text'].includes(field.kind) }" :title="field.path">
          <span>{{ field.label }}</span>
          <textarea v-if="field.kind === 'textarea'" :aria-label="field.path" :value="field.value" :maxlength="field.maxLength" rows="4" placeholder="Enter a task…" @input="change(field, $event)"></textarea>
          <select v-else-if="field.kind === 'select'" :aria-label="field.path" :value="field.value" @change="change(field, $event)"><option v-for="value in field.options" :key="value" :value="value">{{ value || 'None' }}</option></select>
          <input v-else :aria-label="field.path" :type="field.kind === 'boolean' ? 'checkbox' : field.kind === 'number' ? 'number' : 'text'" :value="field.value" :checked="field.kind === 'boolean' && field.value" :min="field.min" :max="field.max" :step="field.step || 1" @input="change(field, $event)" />
        </label>
      </template>
    </fieldset>
    <div v-if="data.id === 'tools'" class="node-tool-summary"><span>Available actions</span><div v-for="([name, value]) in data.tools" :key="name" :title="value">{{ name }}</div><small v-if="!data.tools.length">No environment tools enabled</small></div>
    <div v-if="data.id === 'answer'" class="node-result nodrag nowheel" @pointerdown.stop>
      <template v-if="data.result"><dl><template v-for="name in ['status', 'steps', 'model_calls', 'elapsed_seconds', 'reward']" :key="name"><dt>{{ name }}</dt><dd>{{ name === 'elapsed_seconds' ? Number(data.result[name]).toFixed(3) + ' s' : data.result[name] ?? '—' }}</dd></template></dl><pre>{{ data.result.answer ?? 'No final answer' }}</pre><p v-if="data.result.error" class="error-text">{{ data.result.error }}</p><small>usage {{ JSON.stringify(data.result.usage || {}) }}</small></template>
      <p v-else>No run result</p>
    </div>
    <Handle v-for="port in data.ports" :id="port.id" :key="`${port.direction}-${port.id}`" :type="port.direction" :position="port.side === 'left' ? Position.Left : Position.Right" :connectable="!data.readOnly" :class="{ 'port-picked': data.pendingPort?.nodeId === data.id && data.pendingPort?.handleId === port.id, 'port-compatible': data.pendingPort && data.pendingPort.nodeId !== data.id && data.pendingPort.type === port.type && data.pendingPort.direction !== port.direction }" :title="`${port.id}: ${port.type} · click or drag to connect`" @click.stop="emit('port-click', port.id, $event)" :style="{ top: `${port.offset}px` }" @contextmenu.prevent.stop="emit('disconnect-port', port.id)" />
  </article>
</template>
