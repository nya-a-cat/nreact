<script setup>
import { X } from '@lucide/vue'

defineProps({ theme: String, configName: String, busy: Boolean, tomlDirty: Boolean, error: String, notice: String })
const emit = defineEmits(['theme', 'close'])
</script>

<template>
  <div class="modal-scrim" @click.self="emit('close')">
    <section class="help-dialog settings-dialog" role="dialog" aria-modal="true" aria-label="Settings">
      <header><h2>Settings</h2><button aria-label="Close settings" @click="emit('close')"><X :size="18" /></button></header>
      <h3>Appearance</h3>
      <label class="theme-setting" for="workbench-theme">
        <span>Theme</span>
        <select id="workbench-theme" :value="theme" :disabled="busy || tomlDirty" autofocus @change="emit('theme', $event)">
          <option value="classic">Classic</option>
          <option value="graphite">Graphite</option>
        </select>
      </label>
      <p class="theme-description">{{ theme === 'graphite' ? 'Charcoal panels, dark fields and gold connections.' : 'Warm gray nodes, cream fields and magenta accents.' }}</p>
      <p v-if="tomlDirty" class="settings-hint">Save or reload your TOML draft to change the theme.</p>
      <p v-else class="settings-hint">Saved automatically to {{ configName }}.</p>
      <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
      <footer><span role="status">{{ busy ? 'Saving theme…' : notice }}</span><button class="outline" @click="emit('close')">Done</button></footer>
    </section>
  </div>
</template>

<style scoped>
.settings-dialog { width: min(440px, calc(100vw - 40px)); }
.settings-dialog h3 { margin: 24px 0 16px; padding-top: 18px; border-top: 1px solid var(--line); font-size: 12px; font-weight: 600; color: var(--cream); }
.theme-setting { display: flex; align-items: center; justify-content: space-between; gap: 20px; font-size: 13px; }
.theme-setting select { width: 180px; max-width: 65%; }
.settings-dialog .theme-description { margin: 12px 0 20px; }
.settings-dialog .settings-hint { margin-bottom: 18px; font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.settings-dialog .settings-error { color: #f3b7cb; }
.settings-dialog footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.settings-dialog footer span { font-size: 11px; color: var(--cream); }
</style>
