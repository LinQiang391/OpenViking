<template>
  <div class="form-section">
    <h4>Config Schema (Dynamic)</h4>
    <p style="color: #6b7280; margin-bottom: 20px;">
      This form is dynamically generated from the config schema. 
      Any new fields added to the schema will automatically appear here.
    </p>
    
    <div v-if="schema && schema.properties">
      <template v-for="(fieldSchema, key) in schema.properties" :key="key">
        <FormField
          :field-key="key"
          :field-schema="fieldSchema"
          :value="config[key]"
          :is-required="schema.required && schema.required.includes(key)"
          @update:value="(v) => updateConfig(key, v)"
        />
      </template>
    </div>
    
    <div v-else>
      <p style="color: #6b7280;">Loading schema...</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import FormField from './FormField.vue'

const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  config: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['update:config'])

function updateConfig(key, value) {
  const newConfig = { ...props.config, [key]: value }
  emit('update:config', newConfig)
}
</script>
