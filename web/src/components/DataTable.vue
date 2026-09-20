<script setup lang="ts">
// 简易表格：表头由 columns 生成，表体行由父组件通过默认插槽自行渲染，
// 既保证表头样式统一，又保留行内容的灵活性（插槽行）。
interface Column {
  key: string
  label: string
  width?: string
  align?: 'left' | 'right' | 'center'
}

defineProps<{ columns: Column[] }>()
</script>

<template>
  <table class="data-table">
    <thead>
      <tr>
        <th
          v-for="c in columns"
          :key="c.key"
          :style="{
            width: c.width,
            textAlign: c.align ?? 'left',
          }"
        >
          {{ c.label }}
        </th>
      </tr>
    </thead>
    <tbody>
      <slot />
    </tbody>
  </table>
</template>
