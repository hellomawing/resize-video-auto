import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物输出到 dist，部署时由 FastAPI 静态托管。
export default defineConfig({
  base: '/',
  plugins: [vue()],
  resolve: {
    alias: {
      // 统一用 @ 指向 src，避免相对路径层层回溯
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
  },
  server: {
    // 开发态把 /api 转发到后端 FastAPI（含 WebSocket，ws:true 必须）。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8099',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
