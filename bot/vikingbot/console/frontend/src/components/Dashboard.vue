<template>
  <div>
    <div class="status-bar">
      <div class="status-item" v-if="status">
        <span class="status-dot"></span>
        Running
      </div>
      <div class="status-item" v-if="status">
        Version: {{ status.version }}
      </div>
      <div class="status-item" v-if="status">
        Sessions: {{ status.sessions_count }}
      </div>
      <div class="status-item" v-else>
        <span class="loading"></span>
        Loading...
      </div>
    </div>

    <div class="card">
      <h3>Quick Stats</h3>
      <div v-if="status" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;">
        <div style="padding: 20px; background: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
          <div style="font-size: 32px; color: #2563eb;">{{ status.sessions_count }}</div>
          <div style="color: #6b7280;">Sessions</div>
        </div>
        <div style="padding: 20px; background: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
          <div style="font-size: 32px; color: #22c55e;">✓</div>
          <div style="color: #6b7280;">Gateway</div>
        </div>
        <div style="padding: 20px; background: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
          <div style="font-size: 14px; color: #6b7280; word-break: break-all;">{{ status.config_path }}</div>
          <div style="color: #9ca3af; margin-top: 5px;">Config Path</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const status = ref(null)

async function loadStatus() {
  try {
    const res = await fetch('/api/v1/status')
    const data = await res.json()
    if (data.success) {
      status.value = data.data
    }
  } catch (e) {
    console.error('Status error:', e)
  }
}

onMounted(() => {
  loadStatus()
})
</script>
