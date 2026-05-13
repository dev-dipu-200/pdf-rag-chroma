<script setup lang="ts">
import type { PdfDocument } from '~/types/api'

definePageMeta({ middleware: 'admin' })

const api = useApi()
const auth = useAuth()

const documents = ref<PdfDocument[]>([])
const page = ref(1)
const pages = ref(1)
const total = ref(0)
const uploadFiles = ref<File[]>([])
const loading = ref(false)
const uploading = ref(false)
const status = ref('')
const error = ref('')

const loadDocuments = async () => {
  loading.value = true
  error.value = ''
  try {
    const result = await api.ingest.listDocuments(page.value, 10)
    documents.value = result.items
    page.value = result.page
    pages.value = result.pages
    total.value = result.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load documents.'
  } finally {
    loading.value = false
  }
}

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  uploadFiles.value = Array.from(input.files || [])
}

const upload = async () => {
  if (uploadFiles.value.length === 0) {
    return
  }

  const formData = new FormData()
  for (const file of uploadFiles.value) {
    formData.append('files', file)
  }

  uploading.value = true
  error.value = ''
  status.value = ''

  try {
    const result = await api.ingest.uploadDocuments(formData)
    status.value = `${result.queued_documents} file(s) queued for indexing.`
    uploadFiles.value = []
    await loadDocuments()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Upload failed.'
  } finally {
    uploading.value = false
  }
}

const reindex = async () => {
  error.value = ''
  status.value = ''
  try {
    const result = await api.ingest.reindex()
    status.value = `${result.queued_documents} document(s) queued for reindexing.`
    await loadDocuments()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Reindex failed.'
  }
}

const remove = async (documentId: number) => {
  error.value = ''
  status.value = ''
  try {
    await api.ingest.deleteDocument(documentId)
    status.value = 'Document deleted.'
    await loadDocuments()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Delete failed.'
  }
}

await auth.restore()
await loadDocuments()
</script>

<template>
  <AppShell>
    <div class="grid gap-6 lg:grid-cols-[420px_1fr]">
      <section class="glass-card rounded-[2rem] p-6">
        <p class="soft-label text-orange-700">Admin</p>
        <h2 class="mt-2 text-3xl font-bold text-slate-900">Document operations</h2>
        <p class="mt-3 text-sm leading-7 text-slate-600">
          Upload PDFs to the shared knowledge base, trigger reindexing, or delete stale documents.
        </p>

        <div class="mt-6 rounded-[1.8rem] bg-slate-950 px-5 py-6 text-white">
          <p class="text-sm font-semibold uppercase tracking-[0.22em] text-white/55">Upload queue</p>
          <p class="mt-3 text-sm leading-7 text-white/80">
            Drop one or more PDFs here, then push them into the indexing pipeline.
          </p>

          <input
            type="file"
            multiple
            accept="application/pdf"
            class="mt-5 block w-full rounded-[1.8rem] border border-dashed border-white/20 bg-white/8 px-4 py-6 text-sm text-white/80 file:mr-4 file:rounded-full file:border-0 file:bg-white file:px-4 file:py-2 file:text-sm file:font-semibold file:text-slate-900"
            @change="onFileChange"
          >

          <div class="mt-5 flex flex-wrap gap-3">
            <button
              class="primary-button disabled:opacity-60"
              :disabled="uploading"
              @click="upload"
            >
              {{ uploading ? 'Uploading...' : 'Upload PDFs' }}
            </button>
            <button
              class="rounded-full border border-white/18 bg-white/8 px-5 py-3 text-sm font-semibold text-white transition hover:bg-white/12"
              @click="reindex"
            >
              Reindex all
            </button>
          </div>
        </div>

        <div class="mt-4 space-y-4">
          <p v-if="status" class="rounded-2xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
            {{ status }}
          </p>
          <p v-if="error" class="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {{ error }}
          </p>
        </div>
      </section>

      <section class="glass-card rounded-[2rem] p-6">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="soft-label text-sky-700">Archive</p>
            <h2 class="mt-2 text-3xl font-bold text-slate-900">Indexed documents</h2>
          </div>
          <div class="rounded-[1.25rem] bg-slate-950 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-slate-900/12">
            Total {{ total }}
          </div>
        </div>

        <div class="mt-6 overflow-x-auto rounded-[1.6rem] border border-white/60 bg-white/65 px-4 py-2">
          <table class="min-w-full text-left text-sm">
            <thead>
              <tr class="border-b border-slate-200 text-slate-500">
                <th class="pb-3 font-medium">Filename</th>
                <th class="pb-3 font-medium">Status</th>
                <th class="pb-3 font-medium">Chunks</th>
                <th class="pb-3 font-medium">Created</th>
                <th class="pb-3 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loading">
                <td colspan="5" class="py-6 text-slate-500">Loading documents...</td>
              </tr>
              <tr v-else-if="documents.length === 0">
                <td colspan="5" class="py-6 text-slate-500">No documents uploaded yet.</td>
              </tr>
              <tr v-for="document in documents" :key="document.id" class="border-b border-slate-100">
                <td class="py-4 pr-4 font-medium text-slate-900">{{ document.original_filename }}</td>
                <td class="py-4 pr-4">
                  <span class="rounded-full px-3 py-1 text-xs font-semibold"
                    :class="{
                      'bg-green-100 text-green-700': document.status === 'indexed',
                      'bg-amber-100 text-amber-700': document.status === 'pending' || document.status === 'indexing',
                      'bg-red-100 text-red-700': document.status === 'failed'
                    }"
                  >
                    {{ document.status }}
                  </span>
                </td>
                <td class="py-4 pr-4 text-slate-600">{{ document.chunks_added }}</td>
                <td class="py-4 pr-4 text-slate-600">{{ new Date(document.created_at).toLocaleString() }}</td>
                <td class="py-4">
                  <button
                    class="rounded-full border border-red-200 px-3 py-2 text-xs font-semibold text-red-600 transition hover:bg-red-50"
                    @click="remove(document.id)"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mt-6 flex items-center justify-between">
          <button
            class="secondary-button px-4 py-2 disabled:opacity-40"
            :disabled="page <= 1"
            @click="page -= 1; loadDocuments()"
          >
            Previous
          </button>
          <p class="text-sm text-slate-600">Page {{ page }} of {{ pages }}</p>
          <button
            class="secondary-button px-4 py-2 disabled:opacity-40"
            :disabled="page >= pages"
            @click="page += 1; loadDocuments()"
          >
            Next
          </button>
        </div>
      </section>
    </div>
  </AppShell>
</template>
