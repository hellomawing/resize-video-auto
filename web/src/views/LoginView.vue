<script setup lang="ts">
import { ref } from 'vue'
import { useAuth } from '../composables/useAuth'
import { useToast } from '../composables/useToast'

const auth = useAuth()
const toast = useToast()

const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  const msg = await auth.login(password.value)
  busy.value = false
  if (msg) {
    error.value = msg
    return
  }
  password.value = ''
  toast.success('已进入')
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <img class="login-logo-img" src="/logo.png" alt="视频无损分割" />
      </div>
      <h1 class="login-title">视频无损分割</h1>
      <p class="login-sub">本工具设置了访问密码，请输入密码进入。</p>

      <form class="login-form" @submit.prevent="submit">
        <input
          v-model="password"
          type="password"
          class="input"
          placeholder="访问密码"
          autocomplete="current-password"
          autofocus
        />
        <div v-if="error" class="login-error">{{ error }}</div>
        <button type="submit" class="btn btn--primary btn--block" :disabled="busy">
          <span v-if="busy" class="spinner" /> 进入
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-5);
  background: var(--color-surface);
}
.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  text-align: center;
}
.login-logo {
  margin-bottom: var(--space-4);
}
.login-logo-img {
  width: 64px;
  height: 64px;
  border-radius: var(--radius);
}
.login-title {
  margin: 0 0 var(--space-2);
  font-size: var(--font-size-lg);
}
.login-sub {
  color: var(--color-text-soft);
  font-size: var(--font-size);
  margin: 0 0 var(--space-5);
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.login-error {
  color: var(--color-danger);
  font-size: var(--font-size);
  text-align: left;
}
</style>