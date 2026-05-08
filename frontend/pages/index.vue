<script setup lang="ts">
const auth = useAuth()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

await auth.restore()

if (auth.token.value) {
  await navigateTo('/chat')
}

const submit = async () => {
  loading.value = true
  error.value = ''

  try {
    if (mode.value === 'login') {
      await auth.login(username.value, password.value)
    } else {
      await auth.register(username.value, password.value)
    }
    await navigateTo('/chat')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Authentication failed.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="mx-auto flex min-h-screen max-w-6xl items-center px-4 py-12 lg:px-8">
    <div class="grid w-full gap-8 lg:grid-cols-[1.15fr_0.85fr]">
      <section class="rounded-[2rem] bg-slate-950 px-8 py-10 text-white shadow-panel">
        <p class="text-sm font-semibold uppercase tracking-[0.3em] text-sky-300">Nuxt 3 Frontend</p>
        <h1 class="mt-4 max-w-xl text-4xl font-bold leading-tight">
          Talk to your PDFs with a clean client on top of the FastAPI API.
        </h1>
        <p class="mt-6 max-w-2xl text-base leading-7 text-slate-300">
          This frontend uses your existing auth, upload, indexing, session, and query endpoints.
          Admins can manage documents; all authenticated users can chat against the indexed knowledge base.
        </p>
        <div class="mt-10 grid gap-4 sm:grid-cols-3">
          <div class="rounded-2xl border border-white/10 bg-white/5 p-4">
            <p class="text-sm font-semibold text-orange-300">Auth</p>
            <p class="mt-2 text-sm text-slate-300">Uses `/auth/login`, `/auth/register-ui`, and `/auth/me`.</p>
          </div>
          <div class="rounded-2xl border border-white/10 bg-white/5 p-4">
            <p class="text-sm font-semibold text-orange-300">Chat</p>
            <p class="mt-2 text-sm text-slate-300">Uses `/query/stream` and chat session endpoints.</p>
          </div>
          <div class="rounded-2xl border border-white/10 bg-white/5 p-4">
            <p class="text-sm font-semibold text-orange-300">Documents</p>
            <p class="mt-2 text-sm text-slate-300">Uses `/ingest/pdfs`, `/ingest/documents`, and `/ingest/reindex`.</p>
          </div>
        </div>
      </section>

      <section class="rounded-[2rem] border border-white/60 bg-white/80 p-8 shadow-panel backdrop-blur">
        <div class="flex rounded-full bg-slate-100 p-1">
          <button
            class="flex-1 rounded-full px-4 py-2 text-sm font-semibold transition"
            :class="mode === 'login' ? 'bg-slate-900 text-white' : 'text-slate-600'"
            @click="mode = 'login'"
          >
            Login
          </button>
          <button
            class="flex-1 rounded-full px-4 py-2 text-sm font-semibold transition"
            :class="mode === 'register' ? 'bg-slate-900 text-white' : 'text-slate-600'"
            @click="mode = 'register'"
          >
            Register
          </button>
        </div>

        <form class="mt-8 space-y-5" @submit.prevent="submit">
          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">Username</label>
            <input
              v-model="username"
              class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-sky-400"
              placeholder="enter username"
            >
          </div>
          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">Password</label>
            <input
              v-model="password"
              type="password"
              class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-sky-400"
              placeholder="enter password"
            >
          </div>

          <p v-if="error" class="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {{ error }}
          </p>

          <button
            type="submit"
            :disabled="loading"
            class="w-full rounded-2xl bg-orange-500 px-4 py-3 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {{ loading ? 'Please wait...' : mode === 'login' ? 'Sign in' : 'Create account' }}
          </button>
        </form>
      </section>
    </div>
  </main>
</template>
