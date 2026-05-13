<script setup>
const auth = useAuth()
const profileMenuOpen = ref(false)

const onLogout = async () => {
  profileMenuOpen.value = false
  auth.logout()
  await navigateTo('/')
}

const toggleProfileMenu = () => {
  profileMenuOpen.value = !profileMenuOpen.value
}

const closeProfileMenu = () => {
  profileMenuOpen.value = false
}
</script>

<template>
  <div class="relative flex min-h-screen w-full flex-col gap-6 overflow-visible px-4 py-5 lg:px-6 lg:py-6">
    <div
      class="pointer-events-none absolute inset-x-0 top-0 -z-10 h-64 rounded-b-[3rem] bg-gradient-to-r from-white/50 via-transparent to-white/40 blur-3xl" />
    <header class="glass-card-strong relative z-40 overflow-visible rounded-[2rem] p-4 lg:p-5">
      <div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div class="flex items-center gap-4">
          <div
            class="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-950 text-sm font-bold text-white shadow-lg shadow-slate-900/20">
            PDF
          </div>
          <div>
            <p class="soft-label">Knowledge Workspace</p>
            <h1 class="text-2xl font-bold text-slate-900">PDF ChatBot</h1>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <NuxtLink to="/chat" class="secondary-button px-4 py-2.5">
            Chat
          </NuxtLink>
          <NuxtLink v-if="auth.user.value?.role === 'admin'" to="/documents" class="secondary-button px-4 py-2.5">
            Documents
          </NuxtLink>
          <div class="relative">
            <button type="button"
              class="flex items-center gap-3 rounded-full bg-slate-950 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-slate-900/15 transition hover:bg-slate-800"
              aria-label="Open profile menu" :aria-expanded="profileMenuOpen" @click="toggleProfileMenu">
              <span
                class="flex h-8 w-8 items-center justify-center rounded-full bg-white/15 text-xs font-semibold uppercase ring-1 ring-white/15">
                {{ auth.user.value?.username?.slice(0, 1) }}
              </span>
              <span aria-hidden="true" class="text-xs opacity-70">▾</span>
            </button>

            <div v-if="profileMenuOpen"
              class="glass-card-strong absolute right-0 top-[calc(100%+0.75rem)] z-50 w-72 rounded-[1.8rem] p-3">
              <div class="rounded-[1.4rem] bg-slate-950 px-4 py-4 text-white">
                <p class="text-xs font-semibold uppercase tracking-[0.2em] text-white/55">Profile</p>
                <p class="mt-1 text-sm font-semibold text-white">{{ auth.user.value?.username }}</p>
                <p class="mt-1 text-xs uppercase tracking-[0.22em] text-white/60">{{ auth.user.value?.role }}</p>
              </div>

              <button type="button"
                class="mt-3 flex w-full items-center justify-between rounded-[1.2rem] px-4 py-3 text-left text-sm font-semibold text-red-600 transition hover:bg-red-50"
                @click="onLogout">
                <span>Logout</span>
                <span aria-hidden="true">↗</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>
    <button v-if="profileMenuOpen" type="button" aria-label="Close profile menu"
      class="fixed inset-0 z-30 cursor-default bg-transparent" @click="closeProfileMenu" />
    <slot />
  </div>
</template>
