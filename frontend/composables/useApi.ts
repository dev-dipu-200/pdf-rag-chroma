import type { AuthResponse, ChatMessage, ChatSession, PaginatedDocuments, QueryResponse, User } from '~/types/api'

type HttpMethod = 'GET' | 'POST' | 'DELETE'

interface ApiOptions extends Omit<RequestInit, 'body' | 'method'> {
  method?: HttpMethod
  body?: BodyInit | Record<string, unknown> | null
  auth?: boolean
}

export function useApi() {
  const config = useRuntimeConfig()
  const token = useState<string>('auth-token', () => '')

  const apiFetch = async <T>(path: string, options: ApiOptions = {}): Promise<T> => {
    const headers = new Headers(options.headers || {})
    const isFormData = process.client && options.body instanceof FormData

    if (!isFormData && options.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }

    if (options.auth !== false && token.value) {
      headers.set('Authorization', `Bearer ${token.value}`)
    }

    const response = await fetch(`${config.public.apiBase}${path}`, {
      ...options,
      headers,
      body: isFormData
        ? (options.body as BodyInit)
        : options.body && typeof options.body === 'object'
          ? JSON.stringify(options.body)
          : (options.body as BodyInit | null | undefined)
    })

    if (!response.ok) {
      let message = `Request failed with status ${response.status}`
      try {
        const payload = await response.json()
        message = payload.detail || payload.message || JSON.stringify(payload)
      } catch {
        message = await response.text()
      }
      throw new Error(message)
    }

    if (response.status === 204) {
      return undefined as T
    }

    return await response.json() as T
  }

  const streamQuery = async (
    payload: { question: string; top_k?: number; session_id?: number | null },
    onLine: (line: Record<string, any>) => void | Promise<void>
  ) => {
    const headers = new Headers({ 'Content-Type': 'application/json' })
    if (token.value) {
      headers.set('Authorization', `Bearer ${token.value}`)
    }

    const response = await fetch(`${config.public.apiBase}/query/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload)
    })

    if (!response.ok || !response.body) {
      let message = `Request failed with status ${response.status}`
      try {
        const data = await response.json()
        message = data.detail || JSON.stringify(data)
      } catch {
        message = await response.text()
      }
      throw new Error(message)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) {
          continue
        }
        await onLine(JSON.parse(trimmed))
      }
    }

    if (buffer.trim()) {
      await onLine(JSON.parse(buffer))
    }
  }

  return {
    apiFetch,
    streamQuery,
    auth: {
      login: (body: { username: string; password: string }) =>
        apiFetch<AuthResponse>('/auth/login', { method: 'POST', body, auth: false }),
      registerUi: (body: { username: string; password: string }) =>
        apiFetch<AuthResponse>('/auth/register-ui', { method: 'POST', body, auth: false }),
      registerAdmin: (body: { username: string; password: string }) =>
        apiFetch<AuthResponse>('/auth/register-admin', { method: 'POST', body, auth: false }),
      me: () => apiFetch<User>('/auth/me')
    },
    query: {
      ask: (body: { question: string; top_k?: number; session_id?: number | null }) =>
        apiFetch<QueryResponse>('/query', { method: 'POST', body }),
      sessions: () => apiFetch<ChatSession[]>('/query/sessions'),
      createSession: () => apiFetch<ChatSession>('/query/sessions', { method: 'POST' }),
      messages: (sessionId: number) =>
        apiFetch<ChatMessage[]>(`/query/sessions/${sessionId}/messages`),
      deleteSession: (sessionId: number) =>
        apiFetch<{ status: string }>(`/query/sessions/${sessionId}`, { method: 'DELETE' }),
      deleteAllSessions: () =>
        apiFetch<{ status: string }>('/query/sessions', { method: 'DELETE' })
    },
    ingest: {
      listDocuments: (page = 1, size = 10) =>
        apiFetch<PaginatedDocuments>(`/ingest/documents?page=${page}&size=${size}`),
      uploadDocuments: (formData: FormData) =>
        apiFetch<{ status: string; queued_documents: number }>('/ingest/pdfs', {
          method: 'POST',
          body: formData
        }),
      reindex: () => apiFetch<{ status: string; queued_documents: number }>('/ingest/reindex', { method: 'POST' }),
      deleteDocument: (documentId: number) =>
        apiFetch<{ status: string }>(`/ingest/documents/${documentId}`, { method: 'DELETE' })
    }
  }
}
