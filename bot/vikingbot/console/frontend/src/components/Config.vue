<template>
  <div class="card">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
      <h3>Configuration</h3>
      <div style="display: flex; gap: 10px;">
        <button class="btn btn-secondary" @click="configMode = configMode === 'form' ? 'json' : 'form'">
          Toggle: {{ configMode === 'form' ? 'Form' : 'JSON' }}
        </button>
        <button class="btn btn-primary" @click="saveConfig">Save Config</button>
      </div>
    </div>

    <div v-if="configMode === 'form'">
      <div class="tabs">
        <button 
          v-for="tab in tabs" 
          :key="tab.id"
          :class="{ active: currentTab === tab.id }"
          @click="currentTab = tab.id"
        >
          {{ tab.name }}
        </button>
      </div>
      <div id="config-form-content">
        <SchemaForm 
          v-if="currentTab === 'schema'"
          :schema="schema"
          :config="config"
          @update:config="(v) => config = v"
        />
        <div v-else>
          <p style="color: #6b7280;">Use Schema tab for dynamic form based on config schema</p>
        </div>
      </div>
    </div>

    <div v-else>
      <textarea 
        v-model="configJson" 
        placeholder="Loading config..."
        style="min-height: 500px;"
      ></textarea>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import SchemaForm from './SchemaForm.vue'

const configMode = ref('form')
const currentTab = ref('schema')
const config = ref({})
const schema = ref(null)

const tabs = [
  { id: 'schema', name: 'Schema (Dynamic)' },
  { id: 'providers', name: 'Providers' },
  { id: 'agents', name: 'Agents' },
  { id: 'channels', name: 'Channels' },
  { id: 'tools', name: 'Tools' },
  { id: 'sandbox', name: 'Sandbox' },
  { id: 'skills', name: 'Skills' },
  { id: 'hooks', name: 'Hooks' }
]

const configJson = computed({
  get: () => JSON.stringify(config.value, null, 2),
  set: (v) => {
    try {
      config.value = JSON.parse(v)
    } catch (e) {
      console.error('Invalid JSON')
    }
  }
})

async function loadConfig() {
  try {
    const [configRes, schemaRes] = await Promise.all([
      fetch('/api/v1/config'),
      fetch('/api/v1/config/schema')
    ])
    
    const configData = await configRes.json()
    const schemaData = await schemaRes.json()
    
    if (configData.success) {
      config.value = configData.data
    }
    if (schemaData.success) {
      schema.value = schemaData.data
    }
  } catch (e) {
    console.error('Config error:', e)
  }
}

async function saveConfig() {
  try {
    const res = await fetch('/api/v1/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: config.value })
    })
    const data = await res.json()
    if (data.success) {
      alert('Config saved! Please restart the gateway service for changes to take effect.')
    }
  } catch (e) {
    alert('Error: ' + e.message)
  }
}

onMounted(() => {
  loadConfig()
})
</script>
