<script setup lang="ts">
import Sanscript from 'sanscript'
import type { ChatMessage, ChatSession } from '~/types/api'

definePageMeta({ middleware: 'auth' })

const auth = useAuth()
const api = useApi()

const sessions = ref<ChatSession[]>([])
const activeSessionId = ref<number | null>(null)
const messages = ref<Array<ChatMessage | { id: string; role: string; content: string; sources: string[] }>>([])
const rawQuestion = ref('')
const sending = ref(false)
const error = ref('')
const loadingSessions = ref(true)
const topK = ref(5)
const hindiTyping = ref(false)

const transliteratedQuestion = computed(() => {
  if (!hindiTyping.value) {
    return rawQuestion.value
  }

  try {
    return Sanscript.t(rawQuestion.value, 'itrans', 'devanagari')
  } catch {
    return rawQuestion.value
  }
})

const loadSessions = async () => {
  loadingSessions.value = true
  try {
    sessions.value = await api.query.sessions()
    if (!activeSessionId.value && sessions.value.length > 0) {
      activeSessionId.value = sessions.value[0].id
      await loadMessages(activeSessionId.value)
    }
  } finally {
    loadingSessions.value = false
  }
}

const loadMessages = async (sessionId: number) => {
  activeSessionId.value = sessionId
  messages.value = await api.query.messages(sessionId)
}

const startNewChat = async () => {
  const session = await api.query.createSession()
  sessions.value = [session, ...sessions.value]
  activeSessionId.value = session.id
  messages.value = []
}

const removeSession = async (sessionId: number) => {
  await api.query.deleteSession(sessionId)
  sessions.value = sessions.value.filter((session) => session.id !== sessionId)
  if (activeSessionId.value === sessionId) {
    activeSessionId.value = sessions.value[0]?.id || null
    messages.value = activeSessionId.value ? await api.query.messages(activeSessionId.value) : []
  }
}

const clearSessions = async () => {
  await api.query.deleteAllSessions()
  sessions.value = []
  activeSessionId.value = null
  messages.value = []
}

const submitQuestion = async () => {
  if (!transliteratedQuestion.value.trim() || sending.value) {
    return
  }

  const text = transliteratedQuestion.value.trim()
  const currentStreamingId = `stream-${Date.now()}`
  error.value = ''
  sending.value = true

  messages.value.push({
    id: `user-${Date.now()}`,
    role: 'user',
    content: text,
    sources: []
  })

  messages.value.push({
    id: currentStreamingId,
    role: 'assistant',
    content: '',
    sources: []
  })

  rawQuestion.value = ''

  try {
    await api.streamQuery(
      {
        question: text,
        top_k: topK.value,
        session_id: activeSessionId.value
      },
      async (event) => {
        if (event.type === 'meta' && event.session_id) {
          activeSessionId.value = event.session_id
        }

        if (event.type === 'token') {
          const target = messages.value.find((message) => message.id === currentStreamingId)
          if (target) {
            target.content += event.content || ''
          }
        }

        if (event.type === 'done') {
          const target = messages.value.find((message) => message.id === currentStreamingId)
          if (target) {
            target.sources = event.sources || []
          }
          await loadSessions()
          if (activeSessionId.value) {
            await loadMessages(activeSessionId.value)
          }
        }

        if (event.type === 'error') {
          throw new Error(event.detail || 'Streaming failed.')
        }
      }
    )
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Streaming failed.'
  } finally {
    sending.value = false
  }
}

await auth.restore()
await loadSessions()
</script>

<template>
  <AppShell>
    <div class="grid flex-1 gap-6 lg:grid-cols-[320px_1fr]">
      <aside class="rounded-[2rem] border border-white/60 bg-white/80 p-5 shadow-panel backdrop-blur">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold text-slate-900">Sessions</h2>
          <button
            class="rounded-full bg-slate-900 px-3 py-2 text-xs font-semibold text-white"
            @click="startNewChat"
          >
            New
          </button>
        </div>

        <div class="mt-4 flex items-center gap-3">
          <label class="text-sm font-medium text-slate-600">Top K</label>
          <input v-model="topK" type="number" min="1" max="10" class="w-20 rounded-xl border border-slate-200 px-3 py-2 text-sm">
          <button
            class="rounded-full border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:border-red-300 hover:text-red-600"
            @click="clearSessions"
          >
            Clear all
          </button>
        </div>

        <div class="mt-5 space-y-3">
          <p v-if="loadingSessions" class="text-sm text-slate-500">Loading sessions...</p>
          <button
            v-for="session in sessions"
            :key="session.id"
            class="block w-full rounded-2xl border px-4 py-3 text-left transition"
            :class="activeSessionId === session.id ? 'border-sky-300 bg-sky-50' : 'border-slate-200 bg-white hover:border-slate-300'"
            @click="loadMessages(session.id)"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="truncate text-sm font-semibold text-slate-900">{{ session.title }}</p>
                <p class="mt-1 text-xs text-slate-500">{{ new Date(session.updated_at).toLocaleString() }}</p>
              </div>
              <span
                class="rounded-full px-2 py-1 text-[10px] font-semibold text-red-600 hover:bg-red-50"
                @click.stop="removeSession(session.id)"
              >
                Delete
              </span>
            </div>
          </button>
        </div>
      </aside>

      <section class="flex min-h-[70vh] flex-col rounded-[2rem] border border-white/60 bg-white/80 p-5 shadow-panel backdrop-blur">
        <div class="flex-1 space-y-4 overflow-y-auto pr-1">
          <div v-if="messages.length === 0" class="rounded-3xl border border-dashed border-slate-300 bg-slate-50 p-10 text-center">
            <h2 class="text-xl font-semibold text-slate-900">Start a conversation</h2>
            <p class="mt-2 text-sm text-slate-600">
              Ask questions about your indexed PDF collection. Admins can upload and reindex from the documents page.
            </p>
          </div>

          <div
            v-for="message in messages"
            :key="message.id"
            class="rounded-3xl p-5"
            :class="message.role === 'user' ? 'ml-auto max-w-3xl bg-slate-900 text-white' : 'mr-auto max-w-4xl bg-slate-50 text-slate-900'"
          >
            <p class="mb-3 text-xs font-semibold uppercase tracking-[0.25em] opacity-70">
              {{ message.role }}
            </p>
            <p class="whitespace-pre-wrap text-sm leading-7">{{ message.content }}</p>
            <div v-if="message.sources?.length" class="mt-4 border-t border-slate-200 pt-3">
              <p class="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Sources</p>
              <ul class="mt-2 space-y-1 text-sm text-slate-600">
                <li v-for="source in message.sources" :key="source">{{ source }}</li>
              </ul>
            </div>
          </div>
        </div>

        <div class="mt-4 border-t border-slate-200 pt-4">
          <p v-if="error" class="mb-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {{ error }}
          </p>
          <div class="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div>
              <p class="text-sm font-semibold text-slate-900">Hindi typing</p>
              <p class="text-xs text-slate-500">Type in Roman Hindi and send Devanagari text.</p>
            </div>
            <button
              type="button"
              class="rounded-full px-4 py-2 text-xs font-semibold transition"
              :class="hindiTyping ? 'bg-slate-900 text-white' : 'border border-slate-200 bg-white text-slate-700'"
              @click="hindiTyping = !hindiTyping"
            >
              {{ hindiTyping ? 'Hindi On' : 'Hindi Off' }}
            </button>
          </div>
          <form class="flex flex-col gap-3 md:flex-row" @submit.prevent="submitQuestion">
            <div class="flex-1 space-y-3">
              <textarea
                v-model="rawQuestion"
                rows="3"
                :placeholder="hindiTyping ? 'Type in Roman Hindi, for example: mera naam dipu hai' : 'Ask something about the uploaded PDFs...'"
                class="min-h-[88px] w-full rounded-3xl border border-slate-200 bg-white px-4 py-3 outline-none transition focus:border-sky-400"
              />
              <div
                v-if="hindiTyping && rawQuestion.trim()"
                class="rounded-3xl border border-orange-200 bg-orange-50 px-4 py-3"
              >
                <p class="text-xs font-semibold uppercase tracking-[0.2em] text-orange-700">Hindi preview</p>
                <p class="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-900">{{ transliteratedQuestion }}</p>
              </div>
            </div>
            <button
              type="submit"
              :disabled="sending"
              class="rounded-3xl bg-orange-500 px-6 py-3 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {{ sending ? 'Sending...' : 'Send' }}
            </button>
          </form>
        </div>
      </section>
    </div>
  </AppShell>
</template>
