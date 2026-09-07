<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

const token = document.querySelector('meta[name="nreact-token"]').content
const page = ref('settings')
const loaded = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const configPath = ref('nreact.toml')
const revision = ref('missing')
const baseline = ref('')
const keyStatus = ref('empty')
const apiKey = ref('')
const clearKey = ref(false)
const workspaceEnabled = ref(false)
const form = ref(null)
const task = ref('')
const starting = ref(false)
const run = ref({ status: 'idle', events: [], result: null })
const config = reactive({ model: {}, agent: {}, tools: { custom: [] } })
let pollTimer

const fileName = computed(() => configPath.value.split(/[/\\]/).pop())
const dirty = computed(() => loaded.value && (
  JSON.stringify(config) !== baseline.value || apiKey.value !== '' || clearKey.value ||
  workspaceEnabled.value !== Boolean(config.tools.workspace)
))
const running = computed(() => starting.value || run.value.status === 'running')
const canRun = computed(() => loaded.value && revision.value !== 'missing' && !dirty.value &&
  !running.value && Boolean(task.value.trim()) && Boolean(config.model.name?.trim()))
const statusText = computed(() => revision.value === 'missing' ? 'Not saved yet' : dirty.value ? 'Unsaved changes' : 'Saved locally')
const keyHint = computed(() => clearKey.value ? 'Saved key will be removed.' :
  keyStatus.value === 'saved' ? 'A key is saved. Leave blank to keep it.' :
  keyStatus.value === 'environment' ? 'A key is available from the environment.' : 'Optional for servers without authentication.')
const usage = computed(() => {
  const data = run.value.result?.usage
  if (!data || !Object.keys(data).length) return '—'
  return data.total_tokens ?? ((data.prompt_tokens ?? 0) + (data.completion_tokens ?? 0))
})

async function request(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Nreact-Token': token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`)
  return data
}

function applySnapshot(data) {
  Object.assign(config, data.config)
  configPath.value = data.path
  revision.value = data.revision
  keyStatus.value = data.key_status
  workspaceEnabled.value = Boolean(config.tools.workspace)
  apiKey.value = ''
  clearKey.value = false
  baseline.value = JSON.stringify(config)
  loaded.value = true
}

async function reload() {
  error.value = ''
  notice.value = ''
  try { applySnapshot(await request('/api/config')) }
  catch (cause) { error.value = cause.message }
}

async function save() {
  if (!form.value.reportValidity()) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const data = JSON.parse(JSON.stringify(config))
    if (!workspaceEnabled.value) data.tools.workspace = ''
    if (workspaceEnabled.value && !data.tools.workspace.trim()) throw new Error('Enter a workspace directory.')
    const action = apiKey.value ? 'replace' : clearKey.value ? 'clear' : 'keep'
    if (action === 'replace') data.model.api_key = apiKey.value
    applySnapshot(await request('/api/config', { config: data, revision: revision.value, api_key_action: action }))
    notice.value = 'Configuration saved.'
  } catch (cause) { error.value = cause.message }
  finally { saving.value = false }
}

function addTool() {
  config.tools.custom.push({ name: '', description: '', callable: '' })
}

async function poll() {
  clearTimeout(pollTimer)
  try {
    run.value = await request('/api/run')
    if (run.value.status === 'running') pollTimer = setTimeout(poll, 500)
  } catch (cause) { error.value = cause.message }
}

async function startRun() {
  if (!canRun.value) return
  starting.value = true
  error.value = ''
  try {
    await request('/api/run', { task: task.value, revision: revision.value })
    await poll()
  } catch (cause) { error.value = cause.message }
  finally { starting.value = false }
}

function selectPage(value) {
  page.value = value
  document.title = value === 'settings' ? 'nreact — Configuration' : 'nreact — Run'
}

onMounted(async () => { await reload(); await poll() })
onUnmounted(() => clearTimeout(pollTimer))
</script>

<template>
  <div class="app-layout">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="selectPage('settings')" aria-label="nreact configuration">
        <img src="/favicon.svg" alt="" width="32" height="32" />
        <span>nreact<span class="version">0.2</span></span>
      </a>
      <div class="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">
        <button :class="['nav-item', { active: page === 'settings' }]" @click="selectPage('settings')" :aria-current="page === 'settings' ? 'page' : undefined">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M8 4v6m8 4v6" /></svg>
          Configuration
        </button>
        <button :class="['nav-item', { active: page === 'run' }]" @click="selectPage('run')" :aria-current="page === 'run' ? 'page' : undefined">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z" /></svg>
          Run agent <span v-if="running" class="running-dot" aria-label="Running"></span>
        </button>
      </nav>
      <div class="sidebar-footer"><span class="local-dot"></span> Local session<span class="footer-note">ReAct · Python + Vue</span></div>
    </aside>

    <main>
      <header class="page-header">
        <div>
          <div class="eyebrow">{{ page === 'settings' ? 'CONFIGURATION' : 'PLAYGROUND' }}</div>
          <h1>{{ page === 'settings' ? 'Set up your agent' : 'Run your agent' }}</h1>
          <p>{{ page === 'settings' ? 'Choose a model and the tools it can use.' : 'Try a task with your saved configuration.' }}</p>
        </div>
        <div v-if="page === 'settings'" class="header-actions">
          <span :class="['save-state', { pending: dirty || revision === 'missing' }]"><span></span>{{ statusText }}</span>
          <button class="button primary" :disabled="!loaded || saving" @click="save">{{ saving ? 'Saving…' : 'Save configuration' }}</button>
        </div>
      </header>

      <div v-if="error" class="message error" role="alert">{{ error }}<button class="text-button" @click="reload">Reload configuration</button></div>
      <div v-if="notice && !dirty && page === 'settings'" class="message success" role="status">{{ notice }}</div>

      <div v-if="!loaded && !error" class="loading" role="status">Loading configuration…</div>

      <form v-if="loaded && page === 'settings'" ref="form" @submit.prevent="save" class="settings-form">
        <div class="settings-grid">
          <section class="panel">
            <div class="panel-heading"><span class="section-number">01</span><div><h2>Model</h2><p>Connect a compatible model endpoint.</p></div></div>
            <label for="model-name">Model name</label>
            <input id="model-name" v-model="config.model.name" placeholder="Your provider's model ID" autocomplete="off" spellcheck="false" maxlength="256" />
            <label for="base-url">API base URL</label>
            <input id="base-url" v-model="config.model.base_url" type="url" required placeholder="https://api.example.com/v1" spellcheck="false" />
            <p class="field-hint">Use the base URL, without /chat/completions.</p>
            <div class="label-row"><label for="api-key">API key</label><span v-if="keyStatus === 'saved'" class="small-tag">Saved</span></div>
            <input id="api-key" v-model="apiKey" type="password" autocomplete="new-password" :placeholder="keyStatus === 'saved' ? 'Saved key · unchanged' : 'Enter an API key'" />
            <div class="key-hint"><p class="field-hint">{{ keyHint }}</p><button v-if="keyStatus === 'saved' && !clearKey" class="text-button" type="button" @click="clearKey = true; apiKey = ''">Remove</button><button v-if="clearKey" class="text-button" type="button" @click="clearKey = false">Undo</button></div>
            <label for="key-env">API key environment variable</label>
            <input id="key-env" v-model="config.model.api_key_env" placeholder="NREACT_API_KEY" autocomplete="off" spellcheck="false" />
            <p class="field-hint">Used when no key is saved above. Saved keys stay in your local TOML file.</p>
          </section>

          <section class="panel">
            <div class="panel-heading"><span class="section-number">02</span><div><h2>Tools</h2><p>Choose what the agent can access.</p></div></div>
            <label class="tool-option"><input type="checkbox" v-model="config.tools.wikipedia" /><span><strong>Wikipedia</strong><small>Search pages and look up matching sentences.</small></span><span class="tool-code">search · lookup</span></label>
            <label class="tool-option"><input type="checkbox" v-model="workspaceEnabled" /><span><strong>Local files</strong><small>Read files and list directories in a workspace.</small></span><span class="tool-code">read · list</span></label>
            <div v-if="workspaceEnabled" class="workspace-field">
              <label for="workspace">Workspace directory</label>
              <input id="workspace" v-model="config.tools.workspace" :required="workspaceEnabled" placeholder=". or an absolute directory" spellcheck="false" />
              <p class="field-hint">Relative paths start at the TOML file's directory. Read content is sent to your model.</p>
            </div>
            <div class="custom-heading"><h3>Custom Python tools</h3><button class="button secondary small" type="button" @click="addTool">+ Add tool</button></div>
            <p v-if="!config.tools.custom.length" class="empty-tools">Connect your own Python functions when you need them.</p>
            <div v-for="(tool, index) in config.tools.custom" :key="index" class="custom-tool">
              <div class="custom-tool-title"><span>Tool {{ index + 1 }}</span><button type="button" class="text-button remove" @click="config.tools.custom.splice(index, 1)" :aria-label="`Remove tool ${index + 1}`">Remove</button></div>
              <label :for="`tool-name-${index}`">Name</label><input :id="`tool-name-${index}`" v-model="tool.name" required placeholder="my_tool" spellcheck="false" />
              <label :for="`tool-handler-${index}`">Python callable</label><input :id="`tool-handler-${index}`" v-model="tool.callable" required placeholder="my_tools:lookup" spellcheck="false" />
              <label :for="`tool-description-${index}`">Description</label><input :id="`tool-description-${index}`" v-model="tool.description" required placeholder="What does this tool do?" />
            </div>
            <p v-if="config.tools.custom.length" class="field-hint">Functions load from the configuration directory or an installed package when you run a task.</p>
          </section>
        </div>

        <details class="panel advanced">
          <summary><div><h2>Execution settings</h2><p>Reasoning mode, limits, and paper examples.</p></div><span class="chevron">⌄</span></summary>
          <div class="advanced-grid">
            <div><label for="mode">Reasoning mode</label><select id="mode" v-model="config.agent.mode"><option value="dense">Dense · thought before each action</option><option value="sparse">Sparse · think as needed</option></select></div>
            <div><label for="paper">Paper examples</label><select id="paper" v-model="config.agent.paper"><option value="">None</option><option value="hotpotqa">HotpotQA</option><option value="fever">FEVER</option></select></div>
            <div><label for="max-steps">Maximum steps</label><input id="max-steps" type="number" min="1" max="1000" required v-model.number="config.agent.max_steps" /></div>
            <div><label for="max-tokens">Tokens per response</label><input id="max-tokens" type="number" min="1" max="131072" required v-model.number="config.model.max_tokens" /></div>
            <div><label for="temperature">Temperature</label><input id="temperature" type="number" min="0" max="2" step="0.1" required v-model.number="config.model.temperature" /></div>
            <div><label for="timeout">Request timeout (seconds)</label><input id="timeout" type="number" min="1" max="300" required v-model.number="config.model.timeout" /></div>
            <div><label for="context-limit">Context limit (characters)</label><input id="context-limit" type="number" min="1" max="2000000" required v-model.number="config.agent.max_context_chars" /></div>
            <div><label for="observation-limit">Observation limit (characters)</label><input id="observation-limit" type="number" min="1" max="250000" required v-model.number="config.agent.max_observation_chars" /></div>
          </div>
          <label class="inline-check"><input type="checkbox" v-model="config.model.send_stop" /> Send stop sequences to the model</label>
        </details>
        <div class="file-footer"><span>Saving to <code :title="configPath">{{ fileName }}</code></span><button type="button" class="text-button" @click="reload">Reload from disk</button></div>
      </form>

      <div v-if="loaded && page === 'run'" class="run-view">
        <div v-if="dirty || revision === 'missing'" class="message warning">Save your configuration before running.<button class="text-button" @click="selectPage('settings')">Open configuration</button></div>
        <form class="panel task-panel" @submit.prevent="startRun">
          <div class="run-model"><span class="local-dot"></span>{{ config.model.name || 'No model configured' }}<span class="small-tag">{{ config.agent.mode }}</span></div>
          <label for="task">Task</label>
          <textarea id="task" v-model="task" placeholder="What would you like the agent to do?" rows="4" maxlength="16000" required></textarea>
          <div class="task-actions"><p class="field-hint">Uses your saved model and tools. Provider usage may be billed.</p><button class="button primary" :disabled="!canRun" type="submit">{{ running ? 'Running…' : 'Run agent' }}<span aria-hidden="true">→</span></button></div>
        </form>

        <section v-if="run.status !== 'idle'" class="panel result-panel" aria-live="polite">
          <div class="result-heading"><h2>{{ running ? 'Agent is working' : 'Run result' }}</h2><span class="small-tag">{{ run.result?.status || run.status }}</span></div>
          <p v-if="run.error || run.result?.error" class="run-error">{{ run.error || run.result.error }}</p>
          <div v-if="run.result?.answer !== null && run.result?.answer !== undefined" class="answer">{{ run.result.answer }}</div>
          <div v-if="run.result" class="metrics"><span><strong>{{ run.result.steps }}</strong> steps</span><span><strong>{{ run.result.model_calls }}</strong> model calls</span><span><strong>{{ usage }}</strong> tokens</span><span><strong>{{ run.result.elapsed_seconds.toFixed(1) }}s</strong> elapsed</span></div>
          <div class="trajectory">
            <p v-if="running && !run.events.length" class="field-hint">Waiting for the model…</p>
            <details v-for="(event, index) in run.events" :key="index" :open="event.kind === 'action' || event.kind === 'error' || event.kind === 'protocol_error'" :class="['event', event.kind]">
              <summary><span class="event-step">{{ event.step }}</span><span>{{ event.kind.replace('_', ' ') }}</span><code v-if="event.tool">{{ event.tool }}</code></summary>
              <pre>{{ event.text }}</pre>
            </details>
          </div>
        </section>
        <div v-else class="run-empty"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 9h18v14H7zM11 14l3 2-3 2m6 0h4" /></svg><h3>Your next run starts here</h3><p>The answer and tool activity will appear below your task.</p></div>
      </div>
    </main>
  </div>
</template>
