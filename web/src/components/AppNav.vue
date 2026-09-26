<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useWebSocket } from '../composables/useWebSocket'

// 左侧固定导航 + 实时连接状态指示
const ws = useWebSocket()

// 「撤销分割」不在这里单列 —— 它是「监控目录」的子页面，入口在那一页的右上角。
// 路由是 /watch/undo，前缀匹配会让「监控目录」这一项在高亮时保持选中。
// 图标为内联 SVG（Lucide 风格），stroke 使用 currentColor 以继承 hover/active 配色。
const links = [
  { to: '/', label: '概览', icon: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>' },
  { to: '/watch', label: '监控目录', icon: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>' },
  { to: '/jobs', label: '任务队列', icon: '<path d="M8 6H21"/><path d="M8 12H21"/><path d="M8 18H21"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>' },
  { to: '/settings', label: '设置', icon: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>' },
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
        <span class="nav-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" v-html="l.icon" />
        </span>
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
  height: 20px;
  text-align: center;
  flex-shrink: 0;
}
.nav-icon svg {
  width: 20px;
  height: 20px;
  display: block;
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
