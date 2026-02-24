<template>
  <div class="container">
    <header>
      <h1>⚓ Vikingbot Console</h1>
      <div class="nav">
        <button 
          v-for="page in pages" 
          :key="page.id"
          :class="{ active: currentPage === page.id }"
          @click="currentPage = page.id"
        >
          {{ page.name }}
        </button>
      </div>
    </header>

    <Dashboard v-if="currentPage === 'dashboard'" />
    <Config v-if="currentPage === 'config'" />
    <Sessions v-if="currentPage === 'sessions'" />
    <Workspace v-if="currentPage === 'workspace'" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import Dashboard from './components/Dashboard.vue'
import Config from './components/Config.vue'
import Sessions from './components/Sessions.vue'
import Workspace from './components/Workspace.vue'

const currentPage = ref('dashboard')

const pages = [
  { id: 'dashboard', name: 'Dashboard' },
  { id: 'config', name: 'Config' },
  { id: 'sessions', name: 'Sessions' },
  { id: 'workspace', name: 'Workspace' }
]
</script>
