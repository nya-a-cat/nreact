<script setup>
import { nextTick, ref, watch } from 'vue'
import { X } from '@lucide/vue'
const props = defineProps({ mode: String, name: String, records: Array, selected: String, preview: Object, busy: Boolean, error: String, replacing: Boolean, truncated: Boolean })
const emit = defineEmits(['close', 'name', 'select', 'save', 'open', 'preview-delete', 'delete', 'apply'])
const dialog = ref(null)
watch(() => props.mode, async () => { await nextTick(); dialog.value?.querySelector('[autofocus]')?.focus() }, { immediate: true })
</script>

<template>
  <div class="modal-scrim" @click.self="!busy && emit('close')">
    <section ref="dialog" class="help-dialog export-dialog" role="dialog" aria-modal="true" aria-label="Workflows">
      <header><h2>{{ mode === 'save' ? 'Save workflow' : mode === 'preview' ? 'Open workflow' : mode === 'delete' ? 'Delete workflow?' : 'Saved workflows' }}</h2><button aria-label="Close workflows" :disabled="busy" @click="emit('close')"><X :size="18" /></button></header>
      <template v-if="mode === 'save'">
        <label class="stacked">Workflow name<input :value="name" aria-label="Workflow name" maxlength="120" autofocus :disabled="busy" @input="emit('name', $event.target.value)" @keydown.enter.prevent="name.trim() && !busy && emit('save')" /></label>
        <p class="field-help">Save model and tool settings, task, node positions and connections in .nreact/workflows/. API key values are omitted. Task and tool content may contain private information.</p>
        <p class="field-help">{{ replacing ? 'This replaces the opened workflow using its last loaded revision.' : 'This creates a new saved workflow.' }} The executable TOML configuration is saved separately.</p>
        <div class="export-actions"><button class="outline" :disabled="busy" @click="emit('close')">Cancel</button><button class="primary" :disabled="busy || !name.trim()" @click="emit('save')">Save workflow</button></div>
      </template>
      <template v-else-if="mode === 'open'">
        <label class="stacked">Saved workflow<select :value="selected" aria-label="Saved workflow" autofocus :disabled="busy || !records.length" @change="emit('select', $event.target.value)"><option value="" disabled>Select a workflow</option><option v-for="item in records" :key="item.id" :value="item.id">{{ item.name }} · {{ item.model || 'No model' }} · {{ new Date(item.updated_at).toLocaleString() }}</option></select></label>
        <p v-if="!records.length" class="field-help">No saved workflows. Use File → Save workflow or Import workflow to get started.</p>
        <p v-if="truncated" class="field-help">Showing the 200 most recently saved workflows.</p>
        <div class="export-actions"><button class="outline" :disabled="busy" @click="emit('close')">Cancel</button><button class="outline" :disabled="busy || !selected" @click="emit('preview-delete')">Delete workflow</button><button class="primary" :disabled="busy || !selected" @click="emit('open')">Open selected workflow</button></div>
      </template>
      <template v-else-if="mode === 'preview' && preview">
        <p class="export-name">{{ preview.workflow.name }}</p>
        <p class="field-help">Opening replaces the current property, TOML and task drafts, and loads the saved graph. Review the endpoint, workspace and Python handlers below before saving the configuration and running.</p>
        <p class="field-help">Saved API key values will be cleared when you save this configuration. Imported files also clear credential environment variables and OAuth file references. No tools execute while opening.</p>
        <textarea :value="preview.toml" aria-label="Workflow settings preview" readonly spellcheck="false"></textarea>
        <label class="stacked">Task<textarea :value="preview.workflow.task" aria-label="Workflow task preview" readonly></textarea></label>
        <div class="export-actions"><button class="outline" autofocus :disabled="busy" @click="emit('close')">Keep editing</button><button class="primary" :disabled="busy" @click="emit('apply')">Replace working copy</button></div>
      </template>
      <template v-else-if="mode === 'delete'">
        <p class="reload-message">Delete the selected saved workflow? Its file will be removed. The working configuration and recorded runs remain unchanged.</p>
        <div class="export-actions"><button class="outline" autofocus :disabled="busy" @click="emit('close')">Keep workflow</button><button class="primary" :disabled="busy" @click="emit('delete')">Confirm deletion</button></div>
      </template>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      <p v-if="busy" class="field-help" role="status">Working…</p>
    </section>
  </div>
</template>
