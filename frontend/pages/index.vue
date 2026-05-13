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
  <main class="flex min-h-screen w-full items-center px-4 py-10 lg:px-6 lg:py-12">
    <div class="grid w-full gap-8 lg:grid-cols-[1.12fr_0.88fr]">
      <section class="relative overflow-hidden rounded-[2.4rem] bg-slate-950 px-8 py-10 text-white shadow-panel lg:px-10 lg:py-12">
        <div class="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(103,232,249,0.18),transparent_24%),radial-gradient(circle_at_80%_20%,rgba(251,191,36,0.22),transparent_18%),linear-gradient(160deg,#08111f_0%,#0f1b2d_100%)]" />
        <div class="absolute -right-12 top-10 h-52 w-52 rounded-full border border-white/10 bg-white/5 blur-sm" />
        <div class="absolute bottom-0 left-10 h-24 w-24 rounded-full bg-orange-400/20 blur-2xl" />

        <div class="relative">
          <p class="text-sm font-semibold uppercase tracking-[0.34em] text-sky-200">Realistic PDF Workspace</p>
          <h1 class="mt-5 max-w-2xl text-5xl font-bold leading-[1.02] text-white lg:text-6xl">
            Search, ask, and manage documents in one refined AI desk.
          </h1>
          <p class="mt-6 max-w-2xl text-base leading-8 text-slate-300 lg:text-lg">
            A warmer, more premium interface for your FastAPI PDF chatbot. Sign in, open a session,
            and query indexed files without the UI feeling like a starter template.
          </p>

          <div class="mt-10 grid gap-4 sm:grid-cols-3">
            <div class="rounded-[1.6rem] border border-white/10 bg-white/8 p-5 backdrop-blur-sm">
              <p class="text-sm font-semibold text-orange-300">Auth</p>
              <p class="mt-2 text-sm leading-6 text-slate-300">Login, register, and restore sessions cleanly.</p>
            </div>
            <div class="rounded-[1.6rem] border border-white/10 bg-white/8 p-5 backdrop-blur-sm">
              <p class="text-sm font-semibold text-orange-300">Chat</p>
              <p class="mt-2 text-sm leading-6 text-slate-300">Stream answers from indexed PDFs in real time.</p>
            </div>
            <div class="rounded-[1.6rem] border border-white/10 bg-white/8 p-5 backdrop-blur-sm">
              <p class="text-sm font-semibold text-orange-300">Documents</p>
              <p class="mt-2 text-sm leading-6 text-slate-300">Upload, review, and reindex the shared archive.</p>
            </div>
          </div>

          <div class="mt-10 flex flex-wrap gap-3">
            <div class="rounded-full border border-white/12 bg-white/10 px-4 py-2 text-sm text-white/90">FastAPI backend</div>
            <div class="rounded-full border border-white/12 bg-white/10 px-4 py-2 text-sm text-white/90">Nuxt 3 frontend</div>
            <div class="rounded-full border border-white/12 bg-white/10 px-4 py-2 text-sm text-white/90">Chroma-backed retrieval</div>
          </div>
        </div>
      </section>

      <section class="glass-card-strong rounded-[2.4rem] p-8 lg:p-9">
        <div class="mb-6">
          <p class="soft-label">Secure Entry</p>
          <h2 class="mt-3 text-3xl font-bold text-slate-900">Access your archive</h2>
          <p class="mt-3 text-sm leading-7 text-slate-600">
            Use your existing account or create a new one to start querying the document base.
          </p>
        </div>

        <div class="flex rounded-full bg-slate-100/85 p-1.5">
          <button
            class="flex-1 rounded-full px-4 py-3 text-sm font-semibold transition"
            :class="mode === 'login' ? 'bg-slate-950 text-white shadow-lg shadow-slate-900/15' : 'text-slate-600'"
            @click="mode = 'login'"
          >
            Login
          </button>
          <button
            class="flex-1 rounded-full px-4 py-3 text-sm font-semibold transition"
            :class="mode === 'register' ? 'bg-slate-950 text-white shadow-lg shadow-slate-900/15' : 'text-slate-600'"
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
              class="field-input"
              placeholder="enter username"
            >
          </div>
          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">Password</label>
            <input
              v-model="password"
              type="password"
              class="field-input"
              placeholder="enter password"
            >
          </div>

          <p v-if="error" class="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {{ error }}
          </p>

          <button
            type="submit"
            :disabled="loading"
            class="primary-button w-full rounded-[1.35rem] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {{ loading ? 'Please wait...' : mode === 'login' ? 'Sign in' : 'Create account' }}
          </button>

          <div class="rounded-[1.6rem] bg-slate-950/4 px-4 py-4">
            <p class="text-sm font-semibold text-slate-900">What you get</p>
            <p class="mt-2 text-sm leading-6 text-slate-600">
              Persistent sessions, PDF-grounded answers, and an admin workspace for managing the retrieval archive.
            </p>
          </div>
        </form>
      </section>
    </div>
  </main>
</template>
