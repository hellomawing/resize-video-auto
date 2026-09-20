<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useWebSocket } from '../composables/useWebSocket'

// 左侧固定导航 + 实时连接状态指示
const ws = useWebSocket()

const links = [
  { to: '/', label: '概览', icon: '◎' },
  { to: '/watch', label: '监控目录', icon: '📁' },
  { to: '/schedule', label: '定时任务', icon: '⏰' },
  { to: '/jobs', label: '任务队列', icon: '📋' },
  { to: '/settings', label: '设置', icon: '⚙' },
  { to: '/undo', label: '撤销分割', icon: '↩' },
]
</script>

<template>
  <aside class="nav">
    <div class="nav-brand">
      <div class="nav-logo">VS</div>
      <div class="nav-brand-text">
        <div class="nav-brand-title">视频无损分割</div>
        <div class="nav-brand-sub">NAS 控制台</div>
      </div>
    </div>

    <nav class="nav-links">
      <RouterLink
        v-for="l in links"
        :key="l.to"
        :to="l.to"
        class="nav-link"
        active-class="nav-link--active"
        :exact-active-class="l.to === '/' ? 'nav-link--active' : ''"
      >
        <span class="nav-icon">{{ l.icon }}</span>
        <span>{{ l.label }}</span>
      </RouterLink>
    </nav>

    <div class="nav-foot">
      <span class="nav-dot" :class="ws.connected.value ? 'is-on' : 'is-off'" />
      {{ ws.connected.value ? '实时连接已建立' : '实时连接断开，重连中…' }}
    </div>
  </aside>
</template>

<style scoped>
.nav {
  width: 220px;
  flex-shrink: 0;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  padding: var(--space-4) 0;
  position: sticky;
  top: 0;
  height: 100vh;
}
.nav-brand {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-4) var(--space-4);
}
.nav-logo {
  width: 38px;
  height: 38px;
  border-radius: var(--radius);
  background: var(--color-primary);
  color: #fff;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.nav-brand-title {
  font-weight: 600;
  font-size: var(--font-size);
}
.nav-brand-sub {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.nav-links {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 var(--space-2);
  flex: 1;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-3);
  border-radius: var(--radius);
  color: var(--color-text-soft);
  font-size: var(--font-size);
}
.nav-link:hover {
  background: var(--color-surface-2);
  text-decoration: none;
  color: var(--color-text);
}
.nav-link--active {
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
  font-weight: 500;
}
.nav-icon {
  width: 20px;
  text-align: center;
}
.nav-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4) 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  border-top: 1px solid var(--color-border);
  margin-top: var(--space-3);
}
.nav-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.nav-dot.is-on {
  background: var(--color-success);
}
.nav-dot.is-off {
  background: var(--color-danger);
}
</style>
