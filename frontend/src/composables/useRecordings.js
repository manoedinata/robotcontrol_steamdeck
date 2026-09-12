import { computed, readonly, ref } from 'vue'
import { useBackendConnection } from './useBackendConnection'

// The recordings library, as the page sees it. State lives at module scope so
// reopening the page shows what was already loaded instead of an empty list
// while it refetches, the same shape useBackendConnection uses.

const {
    recordingsUrl,
    recordingSessionUrl,
    recordingFileUrl,
} = useBackendConnection()

const libraryState = ref('idle')
const library = ref(null)
const libraryError = ref('')

// The session whose files are shown. The list deliberately carries no per-file
// detail -- the backend does not probe for it -- so opening one is a second
// request.
const openSessionId = ref(null)
const openSession = ref(null)
const detailState = ref('idle')
const detailError = ref('')

const sessions = computed(() => library.value?.sessions ?? [])
const available = computed(() => library.value?.available !== false)

let detailRequest = 0

async function readJson(url) {
    const response = await fetch(url)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
        throw new Error(payload?.error || `status ${response.status}`)
    }
    return payload
}

async function loadLibrary() {
    libraryState.value = 'loading'
    try {
        library.value = await readJson(recordingsUrl)
        libraryError.value = ''
        libraryState.value = 'ready'
        // The open session may have been deleted elsewhere, or the card pulled.
        if (openSessionId.value && !sessions.value.some((s) => s.id === openSessionId.value)) {
            closeSession()
        }
    } catch (error) {
        console.warn('[recordings] Could not read the library:', error)
        libraryError.value = String(error.message ?? error)
        libraryState.value = 'error'
    }
}

async function selectSession(sessionId) {
    if (openSessionId.value === sessionId) {
        closeSession()
        return
    }
    openSessionId.value = sessionId
    openSession.value = null
    detailState.value = 'loading'
    detailError.value = ''

    // Opening a second session before the first answers would otherwise let the
    // slower response win and show the wrong files.
    const request = (detailRequest += 1)
    try {
        const payload = await readJson(recordingSessionUrl(sessionId))
        if (request !== detailRequest) return
        openSession.value = payload?.session ?? null
        detailState.value = 'ready'
    } catch (error) {
        if (request !== detailRequest) return
        console.warn('[recordings] Could not read session', sessionId, error)
        detailError.value = String(error.message ?? error)
        detailState.value = 'error'
    }
}

function closeSession() {
    detailRequest += 1
    openSessionId.value = null
    openSession.value = null
    detailState.value = 'idle'
    detailError.value = ''
}

async function deleteSession(sessionId) {
    const response = await fetch(recordingSessionUrl(sessionId), { method: 'DELETE' })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
        throw new Error(payload?.error || `status ${response.status}`)
    }
    if (openSessionId.value === sessionId) closeSession()
    await loadLibrary()
    return payload
}

export function useRecordings() {
    return {
        libraryState: readonly(libraryState),
        libraryError: readonly(libraryError),
        library: readonly(library),
        sessions,
        available,
        openSessionId: readonly(openSessionId),
        openSession: readonly(openSession),
        detailState: readonly(detailState),
        detailError: readonly(detailError),
        loadLibrary,
        selectSession,
        closeSession,
        deleteSession,
        recordingFileUrl,
    }
}
