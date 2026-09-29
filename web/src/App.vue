<script setup lang="ts">
import { onMounted, watch } from 'vue'
import LoginView from './views/LoginView.vue'
import AppNav from './components/AppNav.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useJobStore } from './composables/useJobStore'
import { useToast } from './composables/useToast'
import { useAuth } from './composables/useAuth'

// 访问密码门控：启动时先查后端，若需要密码且本机未登录，整页换成登录框。
// 只有通过登录后才建立 WebSocket 和任务状态（它们会拉数据、占连接）。
const auth = useAuth()
const ws = useWebSocket()
const jobStore = useJobStore()
const toast = useToast()
const isAuthed = () => auth.state.value === 'authed' || auth.state.value === 'open'

onMounted(() => {
  void auth.refresh()
  // 仅引用一次，确保模块副作用（连接 + 订阅）被触发
  void jobStore
  void toast
})

// 登录成功后、或需要密码时建立 WebSocket。
// 用瞬时检查（非 watch）避免在 LoginView 阶段就发起连接。
watch(
  () => auth.state.value,
  (s) => {
    if (s === 'authed' || s === 'open') void ws.connectForAuthed()
  },
)
</script>

<template>
  <!-- 需要密码且未登录：整页登录框，不加载主界面与 WebSocket -->
  <LoginView v-if="auth.state.value === 'locked'" />

  <div v-else class="app-shell">
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
