<script setup lang="ts">
import Sanscript from '@indic-transliteration/sanscript'
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
const questionInput = ref<HTMLTextAreaElement | null>(null)

const transliteratedQuestion = computed(() => {
  if (!hindiTyping.value) {
    return rawQuestion.value
  }

  try {
    return Sanscript.t(rawQuestion.value, 'itrans', 'devanagari', { syncope: true })
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
  error.value = ''
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
  error.value = ''
}

const sendQuestion = async (questionText?: string) => {
  const fallbackText = transliteratedQuestion.value.trim()
  const text = (questionText || fallbackText).trim()

  if (!text || sending.value) {
    return
  }

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

  if (!questionText) {
    rawQuestion.value = ''
  }

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

        if (event.type === 'status') {
          const target = messages.value.find((message) => message.id === currentStreamingId)
          if (target && !target.content) {
            target.content = event.stage === 'retrieving'
              ? 'Searching indexed documents...'
              : 'Generating answer...'
          }
        }

        if (event.type === 'token') {
          const target = messages.value.find((message) => message.id === currentStreamingId)
          if (target) {
            if (
              target.content === 'Searching indexed documents...' ||
              target.content === 'Generating answer...'
            ) {
              target.content = ''
            }
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
    const message = err instanceof Error ? err.message : 'Streaming failed.'
    error.value = message
    const target = messages.value.find((entry) => entry.id === currentStreamingId)
    if (target) {
      target.content = message
    }
  } finally {
    sending.value = false
  }
}

const submitQuestion = async () => {
  await sendQuestion()
}

const resendQuestion = async (content: string) => {
  if (!activeSessionId.value) {
    return
  }
  await sendQuestion(content)
}

const editQuestion = (content: string) => {
  rawQuestion.value = content
  nextTick(() => {
    questionInput.value?.focus()
    questionInput.value?.setSelectionRange(rawQuestion.value.length, rawQuestion.value.length)
  })
}

await auth.restore()
await loadSessions()
</script>

<template>
  <AppShell>
    <div class="grid flex-1 gap-6 lg:grid-cols-[320px_1fr]">
      <aside class="glass-card rounded-[2rem] p-5">
        <div class="flex items-center justify-between">
          <div>
            <p class="soft-label">Workspace</p>
            <h2 class="mt-1 text-2xl font-semibold text-slate-900">Sessions</h2>
          </div>
          <button type="button" title="New chat" aria-label="New chat"
            class="flex h-11 w-11 items-center justify-center rounded-full bg-slate-950 text-white shadow-lg shadow-slate-900/15"
            @click="startNewChat">
            <span aria-hidden="true" class="text-lg leading-none">+</span>
          </button>
        </div>

        <div class="mt-4 flex items-center gap-3">
          <template v-if="auth.user.value?.role === 'admin'">
            <label class="text-sm font-medium text-slate-600">Top K</label>
            <input v-model="topK" type="number" min="1" max="10" class="field-input w-20 rounded-xl px-3 py-2 text-sm">
          </template>
          <button type="button" title="Clear all sessions" aria-label="Clear all sessions"
            class="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white/80 text-base text-slate-600 transition hover:border-red-300 hover:text-red-600"
            @click="clearSessions">
            <span aria-hidden="true">🗑</span>
          </button>
        </div>

        <div class="mt-5 space-y-3">
          <p v-if="loadingSessions" class="text-sm text-slate-500">Loading sessions...</p>
          <button v-for="session in sessions" :key="session.id"
            class="block w-full rounded-[1.35rem] border px-4 py-3 text-left transition duration-200"
            :class="activeSessionId === session.id ? 'border-sky-300 bg-gradient-to-r from-sky-50 to-white shadow-lg shadow-sky-100/60' : 'border-slate-200 bg-white/72 hover:border-slate-300 hover:bg-white'"
            @click="loadMessages(session.id)">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="truncate text-sm font-semibold text-slate-900">{{ session.title }}</p>
                <p class="mt-1 text-xs text-slate-500">{{ new Date(session.updated_at).toLocaleString() }}</p>
              </div>
              <button type="button" title="Delete session" aria-label="Delete session"
                class="flex h-8 w-8 items-center justify-center rounded-full text-sm text-red-600 transition hover:bg-red-50"
                @click.stop="removeSession(session.id)">
                <span aria-hidden="true">✕</span>
              </button>
            </div>
          </button>
        </div>
      </aside>

      <section class="glass-card relative flex min-h-[70vh] flex-col overflow-hidden rounded-[2rem] p-5">
        <div class="pointer-events-none absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-white/35 to-transparent" />
        <div class="flex-1 space-y-4 overflow-y-auto pr-1">
          <div v-if="messages.length === 0"
            class="rounded-[2rem] border border-dashed border-slate-300 bg-white/55 p-10 text-center">
            <p class="soft-label">Ready</p>
            <h2 class="mt-3 text-3xl font-semibold text-slate-900">Start a conversation</h2>
            <p class="mt-3 text-sm leading-7 text-slate-600">
              Ask questions about your indexed PDF collection. Admins can upload and reindex from the documents page.
            </p>
          </div>

          <div v-for="message in messages" :key="message.id" class="rounded-[1.8rem] p-5 shadow-sm" :class="message.role === 'user'
            ? 'ml-auto max-w-3xl bg-[linear-gradient(135deg,#101a2c_0%,#1b2b43_100%)] text-white shadow-xl shadow-slate-900/12'
            : 'mr-auto max-w-4xl border border-white/70 bg-white/72 text-slate-900'">
            <p class="mb-3 text-xs font-semibold uppercase tracking-[0.25em] opacity-65">
              {{ message.role }}
            </p>
            <p class="whitespace-pre-wrap text-sm leading-7">{{ message.content }}</p>
            <div v-if="message.role === 'user' && message.content" class="mt-4 flex items-center justify-end gap-2">
              <button type="button" title="Ask again in this session" aria-label="Ask again in this session"
                class="flex h-9 w-9 items-center justify-center rounded-full border border-white/20 text-sm text-white transition hover:border-white/40 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="sending" @click="resendQuestion(message.content)">
                <span aria-hidden="true">↻</span>
              </button>
              <button type="button" title="Edit question" aria-label="Edit question"
                class="flex h-9 w-9 items-center justify-center rounded-full border border-white/20 text-sm text-white transition hover:border-white/40 hover:bg-white/10"
                @click="editQuestion(message.content)">
                <span aria-hidden="true">✎</span>
              </button>
            </div>
            <div v-if="message.sources?.length" class="mt-4 border-t border-slate-200/80 pt-3">
              <p class="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Sources</p>
              <ul class="mt-2 space-y-2 text-sm text-slate-600">
                <li v-for="source in message.sources" :key="source" class="rounded-2xl bg-slate-50/90 px-3 py-2">
                  {{ source }}
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div class="mt-4 border-t border-slate-200 pt-4">
          <p v-if="error" class="mb-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {{ error }}
          </p>
          <div class="mb-3 flex justify-end">
            <button type="button" class="rounded-full px-4 py-2 text-xs font-semibold transition"
              :class="hindiTyping ? 'bg-slate-950 text-white shadow-lg shadow-slate-900/10' : 'border border-slate-200 bg-white text-slate-700'"
              @click="hindiTyping = !hindiTyping">
              {{ hindiTyping ? 'Hindi On' : 'Hindi Off' }}
            </button>
          </div>
          <form class="flex flex-col gap-3 md:flex-row" @submit.prevent="submitQuestion">
            <div class="flex-1 space-y-3">
              <textarea ref="questionInput" v-model="rawQuestion" rows="3"
                :placeholder="hindiTyping ? 'Type in Roman Hindi, for example: mera naam dipu hai' : 'Ask something about the uploaded PDFs...'"
                class="field-input min-h-[88px] rounded-[1.7rem] bg-white/82" />
              <div v-if="hindiTyping && rawQuestion.trim()"
                class="rounded-[1.6rem] border border-orange-200 bg-orange-50/90 px-4 py-3">
                <p class="text-xs font-semibold uppercase tracking-[0.2em] text-orange-700">Hindi preview</p>
                <p class="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-900">{{ transliteratedQuestion }}</p>
              </div>
            </div>
            <button type="submit" :disabled="sending"
              class="primary-button rounded-[1.7rem] px-6 disabled:cursor-not-allowed disabled:opacity-60">
              {{ sending ? 'Sending...' : 'Send' }}
            </button>
          </form>
        </div>
      </section>
    </div>
  </AppShell>
</template>
