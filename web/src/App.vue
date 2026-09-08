<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Blocks, Layers, History, Code, Search, Plus, Play, Pause, Square, StepForward, ChevronLeft, ChevronRight, Save, RotateCcw, Download, X, CircleHelp, Terminal, SlidersHorizontal, Eye, Check, ArrowLeft, FileText, Settings } from '@lucide/vue'
import GraphCanvas from './GraphCanvas.vue'
import SettingsDialog from './SettingsDialog.vue'
import { workflow, eventNode } from './graph.js'

const graphSchema = ref(null)
const config = ref(null), baseline = ref(''), revision = ref(''), configPath = ref(''), keyStatus = ref('empty')
// Appearance follows the working configuration while inspecting any run snapshot.
watch(() => config.value?.ui?.theme, theme => {
  document.documentElement.dataset.theme = theme || 'classic'
}, { immediate: true })
const configName = computed(() => configPath.value.split(/[\\/]/).pop() || 'nreact.toml')
const source = ref(''), savedSource = ref(''), key = ref(''), keyAction = ref('keep')
const selected = ref('agent'), panel = ref('components'), rightTab = ref('properties'), bottomTab = ref('events')
const task = ref(''), search = ref(''), graph = ref(null), runs = ref([]), run = ref(null), activeId = ref(null), eventIndex = ref(-1), followLive = ref(true)
const error = ref(''), notice = ref(''), busy = ref(false), menu = ref(''), help = ref(false)
const sidebar = ref(false), inspectorOpen = ref(false), timeline = ref(false)
const compact = ref(window.innerWidth < 1250), narrow = ref(window.innerWidth < 1000)
const layers = ref({ config: true, action: true })
const graphStatus = ref({ valid: true, missing: [], connections: [] })
const eventList = ref(null)
const exported = ref(null), copyStatus = ref('')
const confirmReload = ref(false)
const settingsOpen = ref(false), settingsError = ref(''), settingsNotice = ref('')
const modalOpen = computed(() => confirmReload.value || !!exported.value || help.value || settingsOpen.value)
let previousFocus
watch(modalOpen, async open => {
  if (open) previousFocus = document.activeElement
  await nextTick()
  if (open) {
    const dialog = document.querySelector('.modal-scrim [role="dialog"]')
    ;(dialog?.querySelector('[autofocus]:not(:disabled)') || dialog?.querySelector('button'))?.focus()
  } else if (previousFocus?.isConnected) previousFocus.focus()
})
const token = document.querySelector('meta[name="nreact-token"]')?.content
const dirty = computed(() => !!config.value && (JSON.stringify(config.value) !== baseline.value || keyAction.value !== 'keep'))
const sourceDirty = computed(() => source.value !== savedSource.value)
const displayed = computed(() => run.value?.config || config.value)
const readOnly = computed(() => !!run.value)
const visibleTask = computed({ get: () => run.value?.task || task.value, set: value => { if (!run.value) task.value = value } })
const events = computed(() => run.value?.events || [])
const currentEvent = computed(() => events.value[eventIndex.value] || null)
const isActive = computed(() => run.value && run.value.id === activeId.value)
const canRun = computed(() => config.value && !readOnly.value && graphStatus.value.valid && !busy.value && !activeId.value && !dirty.value && !sourceDirty.value && revision.value !== 'missing' && config.value.model.name.trim() && task.value.trim())
const runHint = computed(() => activeId.value ? 'Return to the active run to resume or stop it.' : readOnly.value ? 'Return to the working copy to start a new run.' : !graphStatus.value.valid ? `Connect ${graphStatus.value.missing.join(', ')} before running.` : dirty.value || sourceDirty.value || revision.value === 'missing' ? 'Save the configuration before running.' : !config.value?.model.name.trim() ? 'Set model.name in the ChatModel node.' : !task.value.trim() ? 'Enter a task in the Task node.' : 'Run the saved configuration.')
const graphNodes = computed(() => displayed.value ? workflow(displayed.value, '', null, null, graphSchema.value).nodes : [])
const selectedTitle = computed(() => graphNodes.value.find(node => node.id === selected.value)?.data.title || 'Properties')
const status = computed(() => run.value?.status || (dirty.value || sourceDirty.value ? 'Unsaved changes' : revision.value === 'missing' ? 'New configuration' : 'Ready'))
watch([dirty, sourceDirty], ([propertiesChanged, sourceChanged]) => { if (propertiesChanged || sourceChanged) notice.value = '' })
watch([eventIndex, bottomTab, timeline], async () => { await nextTick(); eventList.value?.querySelector('button.chosen')?.scrollIntoView({ block: 'nearest' }) })
const groups = [
  { title: 'Core', items: [{ id: 'task', name: 'Task input', detail: 'The question or instruction', icon: FileText }, { id: 'model', name: 'ChatModel', detail: 'OpenAI-compatible endpoint', icon: Blocks }, { id: 'agent', name: 'Agent', detail: 'Reason · act · observe', icon: SlidersHorizontal }] },
  { title: 'Tools', items: [{ id: 'wikipedia', name: 'Wikipedia', detail: 'Search and look up pages', icon: Search }, { id: 'workspace', name: 'Workspace', detail: 'Read files and list directories', icon: FileText }, { id: 'custom', name: 'Python function', detail: 'Register a module:function', icon: Code }] },
  { title: 'Outputs', items: [{ id: 'answer', name: 'Result', detail: 'Status, answer, usage and errors', icon: Check }] },
]
const filteredGroups = computed(() => groups.map(group => ({ ...group, items: group.items.filter(item => `${item.name} ${item.detail}`.toLowerCase().includes(search.value.toLowerCase())) })).filter(group => group.items.length))
async function api(path, payload) {
  const response = await fetch(`/api/${path}`, { headers: { 'X-Nreact-Token': token, ...(payload ? { 'Content-Type': 'application/json' } : {}) }, ...(payload ? { method: 'POST', body: JSON.stringify(payload) } : {}) })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`)
  return data
}
function applyConfig(data) { graphSchema.value = data.graph_schema; config.value = data.config; baseline.value = JSON.stringify(data.config); revision.value = data.revision; configPath.value = data.path; source.value = savedSource.value = data.toml; keyStatus.value = data.key_status; key.value = ''; keyAction.value = 'keep' }
async function guarded(action) { if (busy.value) return; busy.value = true; error.value = ''; notice.value = ''; try { await action() } catch (reason) { error.value = reason.message } finally { busy.value = false; menu.value = '' } }
async function loadSavedConfig() { confirmReload.value = false; await guarded(async () => { applyConfig(await api('config')); notice.value = 'Configuration reloaded' }) }
function reload() { if (dirty.value || sourceDirty.value) { confirmReload.value = true; menu.value = ''; return }; return loadSavedConfig() }
async function save() { await guarded(async () => {
  if (sourceDirty.value && dirty.value) throw new Error('Both properties and TOML have changes. Save one editor at a time; reload to discard both drafts.')
  const payload = sourceDirty.value ? { toml: source.value, revision: revision.value } : { config: { ...config.value, model: { ...config.value.model, ...(keyAction.value === 'replace' ? { api_key: key.value } : {}) } }, revision: revision.value, api_key_action: keyAction.value }
  applyConfig(await api('config', payload)); notice.value = `Saved to ${configName.value}`
}) }
function editNode(path, value) { if (readOnly.value || sourceDirty.value || busy.value) return; if (path === 'task') { task.value = value; return }; const [section, field] = path.split('.'); if (graphSchema.value.nodes.some(node => node.fields.some(item => item.path === path))) config.value[section][field] = value }
function selectNode(id, inspect = true) { selected.value = id; rightTab.value = 'properties'; if (inspect) inspectorOpen.value = true; if (compact.value) sidebar.value = false }
function togglePanel(id) { sidebar.value = panel.value === id ? !sidebar.value : true; panel.value = id; if (narrow.value && sidebar.value) inspectorOpen.value = false }
function showSource() { rightTab.value = 'source'; inspectorOpen.value = true; if (compact.value) sidebar.value = false }
function showSettings() { settingsError.value = ''; settingsNotice.value = ''; settingsOpen.value = true; menu.value = '' }
async function changeTheme(event) {
  const theme = event.target.value
  if (busy.value || sourceDirty.value || theme === config.value.ui.theme) return
  const restoreFocus = document.activeElement === event.target
  busy.value = true; settingsError.value = ''; settingsNotice.value = ''
  try {
    // Save from the last persisted snapshot so property and credential drafts stay local.
    const saved = JSON.parse(baseline.value)
    saved.ui = { ...saved.ui, theme }
    const data = await api('config', { config: saved, revision: revision.value })
    config.value.ui = data.config.ui
    baseline.value = JSON.stringify(data.config)
    revision.value = data.revision
    source.value = savedSource.value = data.toml
    keyStatus.value = data.key_status
    settingsNotice.value = 'Theme saved'
    notice.value = `Theme saved to ${configName.value}`
  } catch (reason) {
    settingsError.value = reason.message
  } finally {
    busy.value = false
    await nextTick()
    event.target.value = config.value.ui.theme
    if (settingsOpen.value && restoreFocus && document.activeElement === document.body) event.target.focus()
  }
}
function resizeWorkspace() { const nextCompact = window.innerWidth < 1250, nextNarrow = window.innerWidth < 1000; if (nextCompact !== compact.value) sidebar.value = !nextCompact; if (nextNarrow) inspectorOpen.value = false; compact.value = nextCompact; narrow.value = nextNarrow }
function chooseComponent(id) {
  if (['wikipedia', 'workspace', 'custom'].includes(id)) {
    selectNode('tools')
    if (readOnly.value || sourceDirty.value) return
    if (id === 'wikipedia') config.value.tools.wikipedia = true
    if (id === 'workspace' && !config.value.tools.workspace) config.value.tools.workspace = '.'
    if (id === 'custom') {
      const names = new Set(config.value.tools.custom.map(tool => tool.name.toLowerCase()))
      let number = 1
      while (names.has(`tool${number}`)) number++
      config.value.tools.custom.push({ name: `tool${number}`, description: '', callable: '' })
    }
  } else selectNode(id)
}
async function refreshRuns() { const data = await api('runs'); runs.value = data.runs; activeId.value = data.active }
async function openRun(id) { await guarded(async () => { run.value = await api(`run?id=${id}`); eventIndex.value = run.value.events.length - 1; followLive.value = id === activeId.value; rightTab.value = 'event'; timeline.value = true; if (compact.value) sidebar.value = false; if (currentEvent.value) selected.value = eventNode(currentEvent.value) }) }
function workingCopy() { run.value = null; eventIndex.value = -1; rightTab.value = 'properties'; selected.value = 'agent' }
function copyRunTask() { const recordedTask = run.value?.task; workingCopy(); task.value = recordedTask || ''; selectNode('task') }
async function start(demo = false, singleStep = false) { await guarded(async () => { const data = await api('run', { task: task.value, revision: revision.value, demo, single_step: singleStep, ...(!demo ? { connections: graphStatus.value.connections } : {}) }); activeId.value = data.id; await refreshRuns(); run.value = await api(`run?id=${data.id}`); eventIndex.value = run.value.events.length - 1; followLive.value = true; rightTab.value = 'event'; timeline.value = true; if (compact.value) sidebar.value = false; if (narrow.value) inspectorOpen.value = false }) }
async function control(action) { await guarded(async () => { const id = run.value.id; await api('run/control', { id, action }); await refreshRuns(); run.value = await api(`run?id=${id}`); followLive.value = true; eventIndex.value = run.value.events.length - 1; selected.value = eventNode(currentEvent.value) || selected.value }) }
function selectEvent(index, openInspector = true) { eventIndex.value = index; followLive.value = false; selected.value = eventNode(currentEvent.value) || 'agent'; rightTab.value = 'event'; if (openInspector) inspectorOpen.value = true; if (compact.value) sidebar.value = false }
function navigateEvent(delta) { selectEvent(Math.max(0, Math.min(events.value.length - 1, eventIndex.value + delta)), false) }
async function exportFile(kind) { await guarded(async () => { exported.value = await api('export', { kind, ...(kind === 'run' ? { id: run.value.id } : kind === 'graph' ? { graph: graph.value.document() } : {}) }); copyStatus.value = ''; notice.value = 'Export saved to .nreact/exports/' }) }
function exportRun() { if (run.value) exportFile('run') }
async function copyExport(pathOnly = false) { try { await navigator.clipboard.writeText(pathOnly ? exported.value.path : exported.value.content); copyStatus.value = pathOnly ? 'Path copied' : 'Content copied' } catch { copyStatus.value = 'Select the text below and press Ctrl/Cmd+C to copy.' } }
function keydown(event) {
  if (event.key === 'Escape') { if (settingsOpen.value) { settingsOpen.value = false; return }; if (confirmReload.value) { confirmReload.value = false; return }; if (exported.value) { exported.value = null; return }; menu.value = ''; help.value = false; if (compact.value) sidebar.value = false; if (narrow.value) inspectorOpen.value = false; return }
  if (modalOpen.value) {
    if (event.key === 'Tab') {
      const items = [...document.querySelectorAll('.modal-scrim button:not(:disabled), .modal-scrim input:not(:disabled), .modal-scrim select:not(:disabled), .modal-scrim textarea:not(:disabled), .modal-scrim a[href]')]
      const index = items.indexOf(document.activeElement)
      if ((event.shiftKey && index <= 0) || (!event.shiftKey && (index === -1 || index === items.length - 1))) {
        event.preventDefault()
        items[event.shiftKey ? items.length - 1 : 0]?.focus()
      }
    }
    return
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (!busy.value && !readOnly.value) save(); return }
  if (event.target.closest('input,textarea,select,[contenteditable]')) return
  if (event.key.toLowerCase() === 'f') graph.value?.fit()
  if (event.target.closest('.vue-flow')) return
  if (event.key === 'ArrowLeft' && events.value.length) { event.preventDefault(); navigateEvent(-1) }
  if (event.key === 'ArrowRight' && events.value.length) { event.preventDefault(); navigateEvent(1) }
}
function beforeUnload(event) { if (dirty.value || sourceDirty.value) { event.preventDefault(); event.returnValue = '' } }
let poller, polling = false
onMounted(async () => {
  await guarded(async () => { applyConfig(await api('config')); await refreshRuns(); if (activeId.value) { run.value = await api(`run?id=${activeId.value}`); eventIndex.value = run.value.events.length - 1 } })
  window.addEventListener('keydown', keydown); window.addEventListener('beforeunload', beforeUnload); window.addEventListener('resize', resizeWorkspace)
  poller = setInterval(async () => { if (polling || busy.value) return; polling = true; try {
    if (activeId.value) {
      const id = activeId.value
      // Read lifecycle state before the record so a completed run still gets
      // its final snapshot when refreshRuns clears the active id.
      await refreshRuns()
      const data = await api(`run?id=${id}`)
      if (run.value?.id === id) { run.value = data; if (followLive.value) { eventIndex.value = data.events.length - 1; selected.value = eventNode(currentEvent.value) || selected.value } }
    }
  } catch (reason) { error.value = reason.message } finally { polling = false } }, 400)
})
onUnmounted(() => { clearInterval(poller); window.removeEventListener('keydown', keydown); window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('resize', resizeWorkspace) })
</script>

<template>
  <div class="workbench" @click="menu = ''">
    <header class="menubar">
      <nav class="menus" aria-label="Application menu">
        <div v-for="name in ['File', 'View', 'Run', 'Help']" :key="name" class="menu-wrap"><button :class="{ active: menu === name }" @click.stop="menu = menu === name ? '' : name">{{ name }}</button>
          <div v-if="menu === name" class="dropdown" @click.stop>
            <template v-if="name === 'File'"><button :disabled="readOnly || busy" @click="save"><Save :size="14" /> Save configuration <kbd>Ctrl S</kbd></button><button :disabled="busy" @click="reload"><RotateCcw :size="14" /> Reload from disk</button><button :disabled="busy" @click="exportFile('config')"><Download :size="14" /> Export saved TOML</button><button :disabled="readOnly || busy" @click="exportFile('graph')"><Download :size="14" /> Export working graph</button><button :disabled="!run" @click="exportRun"><Download :size="14" /> Export selected run</button><button :disabled="!run" @click="copyRunTask(); menu = ''">Copy run task to working copy</button><button :disabled="!config || busy" @click="showSettings"><Settings :size="14" /> Settings</button></template>
            <template v-if="name === 'View'"><button @click="sidebar = !sidebar; menu = ''">Toggle component panel</button><button @click="timeline = !timeline; menu = ''">Toggle event timeline</button><button @click="graph?.fit(); menu = ''">Fit graph <kbd>F</kbd></button><button @click="graph?.reset(); menu = ''">Reset node positions</button><button :disabled="readOnly" @click="graph?.restoreConnections(); menu = ''">Restore default connections</button><button @click="showSource(); menu = ''">Open TOML editor</button></template>
            <template v-if="name === 'Run'"><button :disabled="!canRun" @click="start()">Run saved configuration</button><button :disabled="!!activeId || busy" @click="start(true, true)">Step through offline demo</button><button :disabled="!!activeId || busy" @click="start(true)">Run offline demo</button></template>
            <template v-if="name === 'Help'"><button @click="help = true; menu = ''">Workbench guide</button><a href="https://github.com/nya-a-cat/nreact/blob/main/docs/configuration.md" target="_blank" rel="noreferrer">Configuration documentation ↗</a></template>
          </div>
        </div>
      </nav>
      <button class="document-tab" :title="configPath" @click="workingCopy"><FileText :size="14" /> {{ configName }} <span class="unsaved">{{ dirty || sourceDirty ? '●' : '' }}</span></button>
      <div class="top-actions"><button aria-label="Save configuration" title="Save configuration (Ctrl S)" :disabled="readOnly || busy || (!dirty && !sourceDirty && revision !== 'missing')" @click="save"><Save :size="14" /><span>Save</span></button><span class="separator"></span>
        <button class="primary" :title="isActive ? 'Resume the paused run' : runHint" :disabled="isActive ? busy || run.status !== 'paused' : !canRun" @click="isActive ? control('resume') : start()"><Play :size="13" />{{ isActive && run.status === 'paused' ? 'Resume' : 'Run' }}</button>
        <button aria-label="Pause" title="Pause after the current turn" :disabled="!isActive || busy || run.status !== 'running'" @click="control('pause')"><Pause :size="14" /><span>Pause</span></button>
        <button :disabled="isActive ? busy || run.status !== 'paused' : !canRun" title="Execute one complete ReAct turn" @click="isActive ? control('step') : start(false, true)"><StepForward :size="15" />Step</button>
        <button aria-label="Stop" title="Stop execution" :disabled="!isActive || busy || run.status === 'cancelling'" @click="control('cancel')"><Square :size="12" /><span>Stop</span></button>
      </div>
    </header>
    <div v-if="error" class="message error" role="alert">{{ error }}<button aria-label="Dismiss error" @click="error = ''"><X :size="15" /></button></div>
    <main v-if="config" class="workspace" :class="{ 'sidebar-hidden': !sidebar, 'timeline-hidden': !timeline, 'inspector-hidden': !inspectorOpen }">
      <aside class="icon-rail" aria-label="Workspace panels"><button v-for="item in [{ id: 'components', icon: Blocks, label: 'Components' }, { id: 'layers', icon: Layers, label: 'Layers' }, { id: 'history', icon: History, label: 'Run history' }]" :key="item.id" :class="{ active: sidebar && panel === item.id }" :title="item.label" :aria-label="item.label" @click="togglePanel(item.id)"><component :is="item.icon" :size="18" /></button><button title="TOML source" aria-label="TOML source" :class="{ active: rightTab === 'source' }" @click="showSource"><Code :size="18" /></button><button title="Toggle inspector" aria-label="Toggle inspector" :class="{ active: inspectorOpen }" @click="inspectorOpen = !inspectorOpen; if (narrow) sidebar = false"><SlidersHorizontal :size="18" /></button><div class="rail-spacer"></div><button title="Settings" aria-label="Settings" :class="{ active: settingsOpen }" @click="showSettings"><Settings :size="18" /></button><button title="Workbench guide" aria-label="Workbench guide" @click="help = true"><CircleHelp :size="18" /></button></aside>
      <aside v-if="sidebar" class="library">
        <header class="panel-heading"><span>{{ panel === 'components' ? 'Components' : panel === 'layers' ? 'Graph layers' : 'Run history' }}</span><button aria-label="Hide side panel" @click="sidebar = false"><X :size="13" /></button></header>
        <template v-if="panel === 'components'">
          <div class="search-field"><Search :size="14" /><input v-model="search" aria-label="Search components" placeholder="Search components…" /></div>
          <div class="library-groups"><section v-for="group in filteredGroups" :key="group.title"><h2>{{ group.title }}</h2><button v-for="item in group.items" :key="item.id" class="component-item" :class="{ chosen: selected === item.id }" @click="chooseComponent(item.id)"><component :is="item.icon" :size="16" /><span><strong>{{ item.name }}</strong><small>{{ item.detail }}</small></span><Plus v-if="group.title === 'Tools'" :size="12" /></button></section><p v-if="!filteredGroups.length" class="muted pad">No matching components.</p></div>
          <div class="library-footer"><span class="eyebrow">LOCAL WORKSPACE</span><p>Connect a model, configure tools, then follow each turn.</p><button class="outline" :disabled="!!activeId || busy" @click="start(true, true)"><StepForward :size="14" /> Try offline demo</button><small>Scripted model · fictional pages</small></div>
        </template>
        <template v-if="panel === 'layers'"><p class="muted pad">Show construction inputs and the run result.</p><label v-for="(_, name) in layers" :key="name" class="layer-switch"><input v-model="layers[name]" type="checkbox" /><i :class="name"></i>{{ name === 'config' ? 'Construction inputs' : 'Run result' }}</label><div class="pad"><button class="outline" @click="graph?.reset()"><RotateCcw :size="14" /> Reset layout</button></div></template>
        <template v-if="panel === 'history'"><div class="history-actions"><button class="outline" @click="workingCopy"><ArrowLeft :size="13" /> Working copy</button><button title="Refresh history" aria-label="Refresh history" @click="guarded(refreshRuns)"><RotateCcw :size="14" /></button></div><div class="run-list"><button v-for="item in runs" :key="item.id" :class="{ chosen: run?.id === item.id }" @click="openRun(item.id)"><span class="run-list-title">{{ item.demo ? 'Offline demo' : item.model }}</span><p>{{ item.task }}</p><small>{{ item.status }} · {{ item.steps }} turns</small><time>{{ new Date(item.started_at).toLocaleString() }}</time></button><p v-if="!runs.length" class="muted pad">Runs appear here after execution. Completed traces remain available after restarting.</p></div></template>
      </aside>
      <section class="center-pane">
        <div class="workspace-tabs"><button class="active" title="Fit all components" @click="graph?.fit()"><Blocks :size="13" /> {{ run ? (run.demo ? 'Offline demo' : 'Run snapshot') : 'Agent graph' }}</button><span v-if="run" class="snapshot-label">{{ run.id.slice(0, 8) }} · read only</span><button v-if="run" class="return-link" @click="workingCopy"><ArrowLeft :size="12" /> Working copy</button><span v-else-if="!activeId" class="snapshot-label">Configuration view</span><button v-if="activeId && !isActive" class="active-run-link" @click="openRun(activeId)"><Play :size="12" /> Return to active run</button></div>
        <GraphCanvas ref="graph" :config="displayed" :task="run?.task || task" :result="run?.result" :schema="graphSchema" :edit-locked="sourceDirty || busy" @edit="editNode" :event="currentEvent" :selected="selected" :layers="layers" :storage-key="configPath" :read-only="readOnly" @select="selectNode" @ready="graphStatus = $event" />
        <section v-if="timeline" class="trace-panel">
          <header class="trace-heading"><div class="trace-tabs"><button :class="{ active: bottomTab === 'events' }" @click="bottomTab = 'events'"><History :size="13" /> Events <span>{{ events.length }}</span></button><button :class="{ active: bottomTab === 'result' }" @click="bottomTab = 'result'"><Terminal :size="13" /> Result</button></div><div class="trace-nav"><button :disabled="eventIndex <= 0" aria-label="Previous event" title="Previous recorded event (←)" @click="navigateEvent(-1)"><ChevronLeft :size="16" /></button><span>{{ events.length ? eventIndex + 1 : 0 }} / {{ events.length }}</span><button :disabled="eventIndex >= events.length - 1" aria-label="Next event" title="Next recorded event (→)" @click="navigateEvent(1)"><ChevronRight :size="16" /></button><button :class="{ active: followLive && isActive }" :disabled="!events.length" @click="followLive = true; eventIndex = events.length - 1; selected = eventNode(currentEvent); rightTab = 'event'">Latest</button><button :disabled="!run" title="Export run JSON" aria-label="Export run" @click="exportRun"><Download :size="14" /></button></div></header>
          <div v-if="!run" class="trace-empty"><Terminal :size="23" /><div><strong>Ready to inspect a run</strong><p>Enter a task in the Task node, or step through the offline demo.</p></div><button :disabled="!!activeId || busy" @click="start(true, true)">Open demo <StepForward :size="14" /></button></div>
          <div v-else-if="bottomTab === 'events'" ref="eventList" class="event-list"><div class="event-columns"><span>TURN</span><span>EVENT</span><span>CONTENT</span><span>ELAPSED</span></div><button v-for="(event, index) in events" :key="index" :class="{ chosen: eventIndex === index }" @click="selectEvent(index)"><span class="mono">{{ String(event.step).padStart(2, '0') }}</span><span class="event-kind" :class="event.kind">{{ event.kind === 'observation' ? 'observation' : event.tool || event.kind }}</span><span class="event-text">{{ event.text }}</span><span class="mono muted">{{ event.elapsed_seconds.toFixed(3) }}s</span></button><p v-if="!events.length" class="muted pad">Waiting for the first model response…</p></div>
          <div v-else class="result-output"><p v-if="run.error" class="error-text">{{ run.error }}</p><pre>{{ run.result?.answer || (isActive ? 'Execution in progress…' : 'This run ended without a final answer.') }}</pre><div v-if="run.result" class="result-stats">{{ run.result.model_calls }} model calls · {{ run.result.steps }} turns · {{ run.elapsed_seconds.toFixed(3) }}s <span v-if="Object.keys(run.result.usage).length">· {{ JSON.stringify(run.result.usage) }}</span></div><p v-if="run.storage_error" class="error-text">{{ run.storage_error }}</p></div>
        </section>
      </section>
      <div v-if="(compact && sidebar) || (narrow && inspectorOpen)" class="panel-backdrop" @click="sidebar = false; if (narrow) inspectorOpen = false"></div><aside v-if="inspectorOpen" class="inspector">
        <section class="outliner"><header class="panel-heading">Graph outliner <span>{{ graphNodes.length }}</span></header><button v-for="(node, index) in graphNodes" :key="node.id" :class="{ chosen: selected === node.id }" @click="selectNode(node.id)"><span class="mono">{{ String(index + 1).padStart(2, '0') }}</span><span>{{ node.data.title }}</span><Eye v-if="selected === node.id" :size="13" /></button></section>
        <header class="inspector-tabs"><button :class="{ active: rightTab === 'properties' }" @click="rightTab = 'properties'">Inspector</button><button :class="{ active: rightTab === 'event' }" @click="rightTab = 'event'">Event</button><button :class="{ active: rightTab === 'source' }" @click="rightTab = 'source'">TOML</button><button class="close-inspector" title="Close inspector" aria-label="Close inspector" @click="inspectorOpen = false"><X :size="14" /></button></header>
        <div v-if="rightTab === 'properties'" class="properties">
          <div v-if="readOnly" class="read-only">Saved run configuration <button @click="workingCopy">Edit working copy ↗</button></div>
          <div v-else-if="sourceDirty" class="read-only">Save or reload the TOML draft to edit properties.</div>
          <h2 class="property-title">{{ selectedTitle }}</h2>
          <fieldset :disabled="readOnly || sourceDirty || busy">
            <template v-if="selected === 'task'"><label class="stacked">Task<textarea v-model="visibleTask" rows="7" placeholder="What should the agent do?" maxlength="16000"></textarea></label><p class="field-help">The task starts a new run using your saved model and tool settings.</p><button class="primary full" :disabled="!canRun" @click="start()"><Play :size="13" /> Run task</button></template>
            <template v-if="selected === 'model'"><label class="stacked">Model name<input v-model="displayed.model.name" placeholder="e.g. local-model" /></label><label class="stacked">Base URL<input v-model="displayed.model.base_url" placeholder="http://127.0.0.1:8080/v1" /></label><label>Temperature<input v-model.number="displayed.model.temperature" type="number" min="0" max="2" step=".1" /></label><label>Max tokens<input v-model.number="displayed.model.max_tokens" type="number" min="1" max="131072" /></label><label>Timeout (seconds)<input v-model.number="displayed.model.timeout" type="number" min="1" max="300" /></label><label>Send stop sequences<input v-model="displayed.model.send_stop" type="checkbox" /></label><h3>Credentials</h3><label class="stacked">Key environment variable<input v-model="displayed.model.api_key_env" /></label><label>API key<select v-model="keyAction"><option value="keep">{{ keyStatus === 'empty' ? 'No key set' : keyStatus === 'saved' ? 'Keep saved key' : 'Use environment' }}</option><option value="replace">Replace key</option><option value="clear">Clear saved key</option></select></label><label v-if="keyAction === 'replace'" class="stacked">New API key<input v-model="key" type="password" autocomplete="new-password" placeholder="Enter key" /></label><p class="field-help">Keys stay out of the TOML preview and configuration snapshots. Clear falls back to the environment variable.</p></template>
            <template v-if="selected === 'agent'"><label>Reasoning mode<select v-model="displayed.agent.mode"><option value="dense">Dense</option><option value="sparse">Sparse</option></select></label><label>Maximum turns<input v-model.number="displayed.agent.max_steps" type="number" min="1" max="1000" /></label><label>Paper examples<select v-model="displayed.agent.paper"><option value="">None</option><option value="hotpotqa">HotpotQA</option><option value="fever">FEVER</option></select></label><h3>Context budget</h3><label>Context characters<input v-model.number="displayed.agent.max_context_chars" type="number" min="1" max="2000000" /></label><label>Observation characters<input v-model.number="displayed.agent.max_observation_chars" type="number" min="1" max="250000" /></label><p class="field-help">Dense mode requests a thought on every turn. Sparse mode lets the model decide when to reason.</p><h3>Task</h3><label class="stacked"><span>Instruction for the next run</span><textarea v-model="visibleTask" rows="4" placeholder="Enter a task…" maxlength="16000"></textarea></label></template>
            <template v-if="selected === 'tools'"><label>Wikipedia<input v-model="displayed.tools.wikipedia" type="checkbox" /></label><p class="field-help">Search and Lookup retrieve Wikipedia pages.</p><label class="stacked">Workspace path<input v-model="displayed.tools.workspace" placeholder="Empty to disable" /></label><p class="field-help">Read and List are restricted to this directory. Relative paths resolve beside the configuration file.</p><h3>Python functions<button title="Add Python function" aria-label="Add Python function" @click="chooseComponent('custom')"><Plus :size="14" /></button></h3><div v-for="(tool, index) in displayed.tools.custom" :key="index" class="tool-form"><label class="stacked">Name<input v-model="tool.name" /></label><label class="stacked">Description<input v-model="tool.description" /></label><label class="stacked">Callable<input v-model="tool.callable" placeholder="my_tools:lookup" /></label><button class="text-button" @click="displayed.tools.custom.splice(index, 1)">Remove function</button></div><p class="field-help">Handlers are imported and executed when a run starts.</p></template>

            <template v-if="selected === 'answer'"><p class="field-help">Finish[answer] ends the loop and returns the model's final response.</p><pre class="inspector-answer">{{ run?.result?.answer || 'No answer yet.' }}</pre></template>
          </fieldset>
        </div>
        <div v-else-if="rightTab === 'event'" class="event-inspector"><template v-if="currentEvent"><h2 class="property-title">{{ currentEvent.kind }} <span class="mono">#{{ eventIndex + 1 }}</span></h2><dl><dt>Turn</dt><dd>{{ currentEvent.step }}</dd><dt>Tool</dt><dd>{{ currentEvent.tool || '—' }}</dd><dt>Elapsed</dt><dd>{{ currentEvent.elapsed_seconds.toFixed(3) }}s</dd></dl><div class="detail-navigation"><button :disabled="eventIndex <= 0" aria-label="Previous event detail" @click="navigateEvent(-1)"><ChevronLeft :size="15" /> Previous</button><span>{{ eventIndex + 1 }} / {{ events.length }}</span><button :disabled="eventIndex >= events.length - 1" aria-label="Next event detail" @click="navigateEvent(1)">Next <ChevronRight :size="15" /></button></div><h3>Recorded content</h3><pre>{{ currentEvent.text }}</pre><p class="field-help">Previous and next browse recorded events. Use Step in the toolbar to execute another turn.</p></template><p v-else class="muted pad">Select an event from the timeline to inspect its full content.</p></div>
        <div v-else class="source-editor"><div class="source-info"><span>{{ readOnly ? 'Working-copy source' : configName }}</span><span>{{ sourceDirty ? 'Modified' : 'Keys omitted' }}</span></div><textarea v-model="source" aria-label="TOML configuration" spellcheck="false" :readonly="readOnly || dirty || busy"></textarea><p v-if="dirty" class="field-help">Save property changes before editing TOML.</p><p v-else class="field-help">Save validates TOML and preserves the existing API key.</p><button class="outline" :disabled="readOnly || busy || !sourceDirty" @click="save"><Save :size="13" /> Save TOML</button></div>
      </aside>
    </main>
    <div v-else class="loading">{{ error ? 'Configuration could not be loaded.' : 'Opening workspace…' }}<button v-if="error" @click="reload">Retry</button></div>
    <footer class="statusbar"><span class="status-dot" :class="{ live: isActive }"></span><strong>{{ status }}</strong><span class="status-message">{{ isActive && run.status === 'pausing' ? 'Finishing the current turn…' : isActive && run.status === 'cancelling' ? 'Waiting for the in-flight call to return…' : notice || (!run ? runHint : '') }}</span><span class="status-shortcuts">Ctrl S Save · F Fit · ← → Inspect</span><span>Python / local</span></footer>
    <SettingsDialog v-if="settingsOpen && config" :theme="config.ui.theme" :config-name="configName" :busy="busy" :toml-dirty="sourceDirty" :error="settingsError" :notice="settingsNotice" @theme="changeTheme" @close="settingsOpen = false" />
    <div v-if="confirmReload" class="modal-scrim" @click.self="confirmReload = false"><section class="help-dialog" role="dialog" aria-modal="true" aria-label="Reload configuration"><header><h2>Reload configuration?</h2><button aria-label="Close reload prompt" @click="confirmReload = false"><X :size="18" /></button></header><p class="reload-message">Your unsaved property and TOML changes will be replaced with the saved file.</p><div class="export-actions"><button class="outline" autofocus @click="confirmReload = false">Keep editing</button><button class="primary" @click="loadSavedConfig">Discard and reload</button></div></section></div>
    <div v-if="exported" class="modal-scrim" @click.self="exported = null"><section class="help-dialog export-dialog" role="dialog" aria-modal="true" aria-label="Export saved"><header><h2>Export saved</h2><button aria-label="Close export" @click="exported = null"><X :size="18" /></button></header><p class="export-name">{{ exported.name }}</p><label class="stacked">Local file<input :value="exported.path" aria-label="Export path" readonly /></label><div class="export-actions"><button class="outline" @click="copyExport(true)">Copy path</button><button class="outline" @click="copyExport()">Copy content</button><span role="status">{{ copyStatus }}</span></div><textarea :value="exported.content" aria-label="Export content" readonly spellcheck="false"></textarea><button class="primary" @click="exported = null">Done</button></section></div>
    <div v-if="help" class="modal-scrim" @click.self="help = false"><section class="help-dialog" role="dialog" aria-modal="true" aria-label="Workbench guide"><header><h2>Workbench guide</h2><button aria-label="Close guide" @click="help = false"><X :size="18" /></button></header><ol><li>Edit <b>ChatModel</b> on the canvas; open its ⋯ menu for credentials.</li><li>Edit <b>ConfiguredEnvironment</b> to enable Wikipedia, a workspace or Python functions.</li><li>Save the configuration, enter a task and click <b>Run</b>.</li><li><b>Step</b> executes one complete ReAct turn. Pause takes effect before the next turn. Stop waits for an in-flight call and prevents the next operation.</li><li>Choose an event to inspect it. The arrow buttons browse saved events without executing tools.</li></ol><p>Try the offline demo to explore the debugger with a scripted model and fictional pages. Runs and traces are stored beside the configuration in <code>.nreact/runs/</code>.</p><p>Drag nodes to arrange the graph. Drag an output port to a matching input to connect; select a wire and press Delete to disconnect. Edit parameters inside nodes; use the ⋯ button for advanced properties. The working graph is saved in this browser. Run requires the backend component connections to be complete.</p><button class="primary" @click="help = false">Got it</button></section></div>
  </div>
</template>
