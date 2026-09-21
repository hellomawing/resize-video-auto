import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
  { path: '/watch', name: 'watch', component: () => import('./views/WatchView.vue') },
  { path: '/jobs', name: 'jobs', component: () => import('./views/JobsView.vue') },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  { path: '/undo', name: 'undo', component: () => import('./views/UndoView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
