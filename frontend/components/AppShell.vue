<script setup lang="ts">
const auth = useAuth()

const onLogout = async () => {
  auth.logout()
  await navigateTo('/')
}
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 px-4 py-6 lg:px-6">
    <header class="rounded-3xl border border-white/60 bg-white/80 p-4 shadow-panel backdrop-blur">
      <div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <p class="text-xs font-semibold uppercase tracking-[0.3em] text-sky-700">Archive Desk</p>
          <h1 class="text-2xl font-bold text-slate-900">PDF RAG Workspace</h1>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <NuxtLink
            to="/chat"
            class="rounded-full border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-sky-300 hover:text-sky-700"
          >
            Chat
          </NuxtLink>
          <NuxtLink
            v-if="auth.user.value?.role === 'admin'"
            to="/documents"
            class="rounded-full border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-orange-300 hover:text-orange-700"
          >
            Documents
          </NuxtLink>
          <div class="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white">
            {{ auth.user.value?.username }} • {{ auth.user.value?.role }}
          </div>
          <button
            class="rounded-full bg-orange-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-orange-600"
            @click="onLogout"
          >
            Logout
          </button>
        </div>
      </div>
    </header>
    <slot />
  </div>
</template>
