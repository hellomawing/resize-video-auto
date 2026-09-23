import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
  { path: '/watch', name: 'watch', component: () => import('./views/WatchView.vue') },
  // 新增 / 编辑监控目录做成了独立页面（都是同一个组件）：
  // 除了路径与扫描方式，还有一个整块的过滤规则要配，塞进弹窗要么被压得看不清、
  // 要么长到内部滚动，而「最后会监控哪个路径」这类关键信息一滚就不见了。
  { path: '/watch/new', name: 'watch-new', component: () => import('./views/WatchFormView.vue') },
  {
    path: '/watch/edit/:id',
    name: 'watch-edit',
    component: () => import('./views/WatchFormView.vue'),
  },
  // 撤销分割是「监控目录」的子页面，不在左侧导航里单列 ——
  // 入口在监控页右上角。菜单高亮靠前缀匹配自动落在「监控目录」上。
  { path: '/watch/undo', name: 'undo', component: () => import('./views/UndoView.vue') },
  { path: '/jobs', name: 'jobs', component: () => import('./views/JobsView.vue') },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  // 老地址留着：书签、浏览器历史、别人发过的链接都还能用
  { path: '/undo', redirect: '/watch/undo' },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
