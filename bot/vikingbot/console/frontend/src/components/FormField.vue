<template>
  <div class="form-group">
    <label class="form-label">
      {{ label }}
      <span v-if="isRequired" class="required">*</span>
    </label>
    
    <input 
      v-if="fieldType === 'boolean'"
      type="checkbox"
      :checked="modelValue"
      @change="handleBooleanChange"
    />
    
    <input 
      v-else-if="fieldType === 'number' || fieldType === 'integer'"
      type="number"
      :value="modelValue"
      @input="handleNumberChange"
    />
    
    <textarea 
      v-else-if="fieldType === 'array'"
      :value="arrayValue"
      @input="handleArrayChange"
      style="min-height: 100px;"
      placeholder="One item per line"
    ></textarea>
    
    <div 
      v-else-if="fieldType === 'object' && fieldSchema.properties"
      style="padding: 15px; background: #f9fafb; border-radius: 6px; border: 1px solid #e5e7eb;"
    >
      <template v-for="(subSchema, subKey) in fieldSchema.properties" :key="subKey">
        <FormField
          :field-key="subKey"
          :field-schema="subSchema"
          :value="modelValue && modelValue[subKey]"
          :is-required="fieldSchema.required && fieldSchema.required.includes(subKey)"
          @update:value="(v) => handleObjectChange(subKey, v)"
        />
      </template>
    </div>
    
    <textarea 
      v-else-if="fieldType === 'object'"
      :value="objectValue"
      @input="handleObjectTextChange"
      style="min-height: 100px;"
      placeholder="Enter JSON object"
    ></textarea>
    
    <textarea 
      v-else-if="isComplexSchema"
      :value="jsonValue"
      @input="handleJsonChange"
      style="min-height: 100px;"
    ></textarea>
    
    <input 
      v-else
      type="text"
      :value="modelValue"
      @input="handleStringChange"
    />
    
    <div v-if="fieldSchema.description" class="form-help">
      {{ fieldSchema.description }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import FormField from './FormField.vue'

const props = defineProps({
  fieldKey: { type: String, required: true },
  fieldSchema: { type: Object, required: true },
  value: { default: undefined },
  isRequired: { type: Boolean, default: false }
})

const emit = defineEmits(['update:value'])

const label = computed(() => props.fieldSchema.title || props.fieldKey)

const fieldType = computed(() => props.fieldSchema.type)

const isComplexSchema = computed(() => 
  props.fieldSchema.anyOf || props.fieldSchema.allOf || props.fieldSchema.oneOf
)

const modelValue = computed(() => props.value)

const arrayValue = computed(() => {
  if (Array.isArray(props.value)) {
    return props.value.join('\n')
  }
  return ''
})

const objectValue = computed(() => {
  if (props.value && typeof props.value === 'object') {
    return JSON.stringify(props.value, null, 2)
  }
  return ''
})

const jsonValue = computed(() => {
  if (props.value !== undefined) {
    return JSON.stringify(props.value, null, 2)
  }
  return ''
})

function handleBooleanChange(e) {
  emit('update:value', e.target.checked)
}

function handleNumberChange(e) {
  const val = parseFloat(e.target.value)
  emit('update:value', isNaN(val) ? undefined : val)
}

function handleStringChange(e) {
  emit('update:value', e.target.value || undefined)
}

function handleArrayChange(e) {
  const str = e.target.value
  if (str) {
    emit('update:value', str.split('\n').map(s => s.trim()).filter(s => s.length > 0))
  } else {
    emit('update:value', [])
  }
}

function handleObjectChange(subKey, value) {
  const current = props.value || {}
  emit('update:value', { ...current, [subKey]: value })
}

function handleObjectTextChange(e) {
  try {
    emit('update:value', JSON.parse(e.target.value))
  } catch (e) {
    // Ignore invalid JSON while typing
  }
}

function handleJsonChange(e) {
  try {
    emit('update:value', JSON.parse(e.target.value))
  } catch (e) {
    // Ignore invalid JSON while typing
  }
}
</script>
