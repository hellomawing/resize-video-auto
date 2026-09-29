<script setup lang="ts">
// 开关：v-model 绑定布尔值，disabled 时禁止切换。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

function toggle(): void {
  if (props.disabled) return
  emit('update:modelValue', !props.modelValue)
}
</script>

<template>
  <button
    type="button"
    class="toggle"
    :class="{ 'toggle--on': modelValue, 'toggle--disabled': disabled }"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="toggle"
  >
    <span class="toggle-knob" />
  </button>
</template>

<style scoped>
.toggle {
  position: relative;
  width: 42px;
  height: 24px;
  border-radius: 999px;
  border: none;
  background: var(--color-border-strong);
  cursor: pointer;
  padding: 0;
  transition: background 0.2s;
  flex-shrink: 0;
}
.toggle--on {
  background: var(--color-primary);
}
.toggle--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.toggle-knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: var(--shadow-sm);
  transition: transform 0.2s;
}
.toggle--on .toggle-knob {
  transform: translateX(18px);
}
</style>
