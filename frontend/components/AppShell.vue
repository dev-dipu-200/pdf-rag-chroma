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
  <div class="relative flex min-h-screen max-w-full flex-col gap-6 overflow-visible px-4 py-6 lg:px-6">
    <header
      class="relative z-40 overflow-visible rounded-3xl border border-white/60 bg-white/80 p-4 shadow-panel backdrop-blur">
      <div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 class="text-2xl font-bold text-slate-900">PDF Chatbot</h1>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <NuxtLink to="/chat"
            class="rounded-full border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-sky-300 hover:text-sky-700">
            Chat
          </NuxtLink>
          <NuxtLink v-if="auth.user.value?.role === 'admin'" to="/documents"
            class="rounded-full border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-orange-300 hover:text-orange-700">
            Documents
          </NuxtLink>
          <div class="relative">
            <button type="button"
              class="flex items-center gap-3 rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800"
              aria-label="Open profile menu" :aria-expanded="profileMenuOpen" @click="toggleProfileMenu">
              <span
                class="flex h-8 w-8 items-center justify-center rounded-full bg-white/15 text-xs font-semibold uppercase">
                {{ auth.user.value?.username?.slice(0, 1) }}
              </span>
              <!-- <span class="hidden sm:block">{{ auth.user.value?.username }}</span> -->
              <span aria-hidden="true" class="text-xs opacity-70">▾</span>
            </button>

            <div v-if="profileMenuOpen"
              class="absolute right-0 top-[calc(100%+0.75rem)] z-50 w-64 rounded-3xl border border-slate-200 bg-white p-3 shadow-2xl">
              <div class="rounded-2xl bg-slate-50 px-4 py-3">
                <p class="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Profile</p>
                <p class="mt-2 text-sm font-semibold text-slate-900">{{ auth.user.value?.username }}</p>
                <p class="mt-1 text-xs text-slate-500">{{ auth.user.value?.role }}</p>
              </div>

              <button type="button"
                class="mt-3 flex w-full items-center justify-between rounded-2xl px-4 py-3 text-left text-sm font-semibold text-red-600 transition hover:bg-red-50"
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
