export interface User {
  id: number
  username: string
  role: string
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}

export interface ChatSession {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  sources: string[]
  created_at: string
}

export interface QueryResponse {
  answer: string
  sources: string[]
  provider?: string | null
  session_id?: number | null
  anonymous_remaining?: number | null
}

export interface PdfDocument {
  id: number
  original_filename: string
  status: string
  chunks_added: number
  error_message?: string | null
  created_at: string
  updated_at: string
}

export interface PaginatedDocuments {
  items: PdfDocument[]
  total: number
  page: number
  pages: number
  size: number
}
