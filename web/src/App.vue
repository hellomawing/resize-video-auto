<script setup lang="ts">
import { onMounted } from 'vue'
import AppNav from './components/AppNav.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useJobStore } from './composables/useJobStore'
import { useToast } from './composables/useToast'

// 在根组件挂载即建立单例 WebSocket 并接线全局任务状态，
// 这样即便不在 /jobs 页面，job.log / job.progress / job.updated 也会实时更新本地状态。
const ws = useWebSocket()
const jobStore = useJobStore()
const toast = useToast()

onMounted(() => {
  // 仅引用一次，确保模块副作用（连接 + 订阅）被触发
  void ws
  void jobStore
  void toast
})
</script>

<template>
  <div class="app-shell">
    <AppNav />
    <main class="app-main">
      <router-view />
    </main>
  </div>

  <!-- 全局轻量提示条 -->
  <div class="toast-wrap">
    <transition-group name="toast">
      <div v-for="t in toast.items" :key="t.id" class="toast" :class="`toast--${t.type}`" @click="toast.remove(t.id)">
        {{ t.message }}
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
}
.app-main {
  flex: 1;
  min-width: 0;
  padding: var(--space-5);
  overflow-x: hidden;
}
.toast-wrap {
  position: fixed;
  top: var(--space-4);
  right: var(--space-4);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.toast {
  min-width: 220px;
  max-width: 360px;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius);
  color: #fff;
  font-size: var(--font-size);
  box-shadow: var(--shadow);
  cursor: pointer;
  line-height: 1.5;
}
.toast--success { background: var(--color-success); }
.toast--error { background: var(--color-danger); }
.toast--info { background: var(--color-primary); }
.toast-enter-active,
.toast-leave-active { transition: all 0.25s ease; }
.toast-enter-from,
.toast-leave-to { opacity: 0; transform: translateX(20px); }
</style>
