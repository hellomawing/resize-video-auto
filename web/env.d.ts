/// <reference types="vite/client" />

// 让 TypeScript 认识 .vue 单文件组件，避免使用 any 兜底。
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}
