<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useWebSocket } from '../composables/useWebSocket'

// 左侧固定导航 + 实时连接状态指示
const ws = useWebSocket()

// 「撤销分割」不在这里单列 —— 它是「监控目录」的子页面，入口在那一页的右上角。
// 路由是 /watch/undo，前缀匹配会让「监控目录」这一项在高亮时保持选中。
const links = [
  { to: '/', label: '概览', icon: '◎' },
  { to: '/watch', label: '监控目录', icon: '📁' },
  { to: '/jobs', label: '任务队列', icon: '📋' },
  { to: '/settings', label: '设置', icon: '⚙' },
]
</script>

<template>
  <aside class="nav">
    <div class="nav-brand">
      <img class="nav-logo" src="/logo.png" alt="视频无损分割" />
      <div class="nav-brand-title">视频无损分割</div>
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
/* 品牌图标源图是 web/public/logo.png：换 logo 只替换这一张图，再重跑 tools/gen_favicon.py */
.nav-logo {
  width: 38px;
  height: 38px;
  border-radius: var(--radius);
  display: block;
  flex-shrink: 0;
}
.nav-brand-title {
  font-weight: 600;
  font-size: var(--font-size);
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
