<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Download,
  FileVideo,
  Play,
  RefreshCw,
  Trash2,
  TriangleAlert,
  X,
} from '@lucide/vue'
import { useRecordings } from '../composables/useRecordings'
import { useBackendConnection } from '../composables/useBackendConnection'
import { useRecordingsGamepadNavigation } from '../composables/useRecordingsGamepadNavigation'
import {
  describeSource,
  describeStatus,
  describeStoppedReason,
  formatBytes,
  formatClockTime,
  formatDuration,
  formatSessionTime,
} from '../utils/formatRecording'

const emit = defineEmits(['close'])

const {
  libraryState,
  libraryError,
  library,
  sessions,
  available,
  openSessionId,
  openSession,
  detailState,
  detailError,
  loadLibrary,
  selectSession,
  deleteSession,
  recordingFileUrl,
} = useRecordings()
const { recordingState } = useBackendConnection()

// The clip on screen, if any. `offset` is what was asked of the backend: a
// remuxed stream is re-based to zero, so the elapsed time the player shows is
// relative to the jump, and the offset has to be added back for display.
const playing = ref(null)
const confirmingDelete = ref(null)
const deleteError = ref('')

// B closes the player first and the page only once nothing is over it.
useRecordingsGamepadNavigation(() => {
  if (playing.value) closePlayer()
  else emit('close')
})

const storageSummary = computed(() => {
  const free = library.value?.free_bytes
  const total = library.value?.total_bytes
  if (typeof free !== 'number' || typeof total !== 'number') return ''
  return `${formatBytes(free)} free of ${formatBytes(total)}`
})

const playingSource = computed(() => {
  if (!playing.value) return ''
  return recordingFileUrl(playing.value.session, playing.value.file, 'play', {
    t: playing.value.offset > 0 ? playing.value.offset.toFixed(3) : null,
    transcode: playing.value.transcode ? 1 : null,
  })
})

function thumbnail(sessionId, part) {
  return recordingFileUrl(sessionId, part.file, 'thumbnail', { w: 160 })
}

function downloadUrl(sessionId, part) {
  return recordingFileUrl(sessionId, part.file, 'download')
}

async function play(sessionId, source, part, offset = 0) {
  const reopening = playing.value !== null
  playing.value = {
    session: sessionId,
    file: part.file,
    sourceId: source.id,
    duration: part.duration,
    // A codec the renderer cannot decode is refused by the backend with a 415
    // rather than played as a black rectangle, so ask for the encode outright.
    transcode: part.playable === false,
    offset,
  }
  // The player is a dialog, so the gamepad has to land in it rather than stay
  // on the card behind. Re-opening at a new offset keeps the focus it has.
  if (reopening) return
  await nextTick()
  document.querySelector('.recording-player [data-gamepad-control]')?.focus({ preventScroll: true })
}

function seekTo(event) {
  if (!playing.value) return
  play(playing.value.session, { id: playing.value.sourceId }, {
    file: playing.value.file,
    duration: playing.value.duration,
    playable: playing.value.transcode ? false : true,
  }, Number(event.target.value))
}

async function closePlayer() {
  playing.value = null
  // Back to the list, rather than leaving focus on a button that is gone.
  await nextTick()
  document.querySelector('#recordings-page .recording-part-actions [data-gamepad-control]')
    ?.focus({ preventScroll: true })
}

function askDelete(sessionId) {
  deleteError.value = ''
  confirmingDelete.value = confirmingDelete.value === sessionId ? null : sessionId
}

async function confirmDelete(sessionId) {
  confirmingDelete.value = null
  try {
    if (playing.value?.session === sessionId) closePlayer()
    await deleteSession(sessionId)
  } catch (error) {
    console.warn('[recordings] Could not delete', sessionId, error)
    deleteError.value = String(error.message ?? error)
  }
}

// A session that is still being written must not be deleted out from under the
// recorder. The backend refuses it too; this only keeps the button honest.
function isActive(session) {
  return session.active === true
}

function partLabel(source, part, index) {
  const position = source.parts.length > 1 ? ` part ${index + 1}` : ''
  return `${source.id}${position}`
}

onMounted(loadLibrary)

// A recording that just stopped is a new entry in this list. Nothing else
// changes the folder while the page is open, so this is the only refresh the
// page needs beyond the operator asking for one.
watch(() => recordingState.value?.active, (now, before) => {
  if (before === true && now !== true) loadLibrary()
})
</script>

<template>
  <div id="recordings-page">
    <div class="recordings-toolbar">
      <p class="recordings-storage">
        <span v-if="available">{{ storageSummary }}</span>
        <span v-else class="recordings-storage-missing">
          <TriangleAlert :size="15" aria-hidden="true" />
          The recordings folder is not available
        </span>
      </p>
      <button type="button" class="btn btn-secondary btn-sm" data-gamepad-control
        :disabled="libraryState === 'loading'" @click="loadLibrary">
        <RefreshCw :size="15" aria-hidden="true" />
        {{ libraryState === 'loading' ? 'Checking...' : 'Refresh' }}
      </button>
    </div>

    <p v-if="deleteError" class="recordings-error" role="alert">{{ deleteError }}</p>

    <p v-if="libraryState === 'error'" class="recordings-empty">
      Could not read the recordings library. Is the backend running?
      <span class="recordings-empty-detail">{{ libraryError }}</span>
    </p>
    <p v-else-if="!available" class="recordings-empty">
      The folder recordings are written to is not there. Insert the card you
      chose in Settings, then refresh.
    </p>
    <p v-else-if="libraryState === 'ready' && !sessions.length" class="recordings-empty">
      Nothing recorded yet. Press the record button to capture every camera at once.
    </p>

    <ul v-if="sessions.length" class="recording-list">
      <li v-for="session in sessions" :key="session.id" class="recording-card"
        :class="{ open: openSessionId === session.id, live: isActive(session) }">
        <button type="button" class="recording-summary" data-gamepad-control
          :aria-expanded="openSessionId === session.id"
          :aria-label="`${formatSessionTime(session.started_at, session.id)}, ${describeStatus(session.status)}, ${formatDuration(session.duration)}, ${formatBytes(session.bytes)}`"
          @click="selectSession(session.id)">
          <span class="recording-chevron" aria-hidden="true">
            <ChevronDown v-if="openSessionId === session.id" :size="18" />
            <ChevronRight v-else :size="18" />
          </span>

          <span class="recording-headline">
            <span class="recording-title" :title="formatClockTime(session.started_at, session.id)">
              {{ formatSessionTime(session.started_at, session.id) }}
            </span>
            <span class="recording-state" :class="`is-${session.status}`">
              {{ describeStatus(session.status) }}
            </span>
          </span>

          <span class="recording-meta">
            <span class="recording-metric">
              <span class="recording-metric-label">Length</span>
              <span class="recording-metric-value">{{ formatDuration(session.duration) }}</span>
            </span>
            <span class="recording-metric">
              <span class="recording-metric-label">Size</span>
              <span class="recording-metric-value">{{ formatBytes(session.bytes) }}</span>
            </span>
            <span class="recording-metric">
              <span class="recording-metric-label">Files</span>
              <span class="recording-metric-value">{{ session.part_count }}</span>
            </span>
          </span>

          <span class="recording-sources">
            <span v-for="source in session.sources" :key="source.id" class="recording-chip"
              :class="{ missing: !source.recorded }" :title="describeSource(source)">
              <CircleAlert v-if="!source.recorded" :size="13" aria-hidden="true" />
              {{ source.id }}
            </span>
            <span v-if="!session.sources.length" class="recording-chip missing">
              No sources recorded
            </span>
          </span>
        </button>

        <div v-if="openSessionId === session.id" class="recording-detail">
          <p v-if="describeStoppedReason(session.stopped_reason)" class="recording-note">
            {{ describeStoppedReason(session.stopped_reason) }}
          </p>
          <p v-else-if="session.status === 'interrupted'" class="recording-note">
            The backend stopped before this recording did, so its last file may be
            shorter than the camera sent.
          </p>
          <p v-else-if="session.status === 'scanned'" class="recording-note">
            This folder has no record of what it holds, so only what the filenames
            say is known.
          </p>

          <p v-if="detailState === 'loading'" class="recording-note">Reading the files...</p>
          <p v-else-if="detailState === 'error'" class="recordings-error" role="alert">
            {{ detailError }}
          </p>

          <div v-for="source in (openSession?.sources ?? [])" :key="source.id"
            class="recording-source">
            <h3 class="recording-source-name">
              {{ source.id }}
              <span v-if="source.kind" class="recording-source-kind">{{ source.kind }}</span>
              <span v-if="source.restarts" class="recording-source-kind">
                {{ source.restarts }} reconnect{{ source.restarts === 1 ? '' : 's' }}
              </span>
            </h3>

            <p v-if="!source.recorded" class="recording-source-empty">
              <CircleAlert :size="15" aria-hidden="true" />
              {{ source.error || 'This camera was recording but produced no video.' }}
            </p>

            <ul v-else class="recording-parts">
              <li v-for="(part, index) in source.parts" :key="part.file" class="recording-part">
                <img class="recording-thumb" :src="thumbnail(session.id, part)" alt="" loading="lazy"
                  width="160" height="90" />
                <span class="recording-part-text">
                  <span class="recording-part-name">{{ partLabel(source, part, index) }}</span>
                  <span class="recording-part-meta">
                    {{ formatDuration(part.duration) }} &middot; {{ formatBytes(part.bytes) }}
                    <template v-if="part.width"> &middot; {{ part.width }}&times;{{ part.height }}</template>
                    <template v-if="part.codec"> &middot; {{ part.codec }}</template>
                  </span>
                </span>
                <span class="recording-part-actions">
                  <button type="button" class="btn btn-secondary btn-sm" data-gamepad-control
                    @click="play(session.id, source, part)">
                    <Play :size="15" aria-hidden="true" />
                    {{ part.playable === false ? 'Convert & play' : 'Play' }}
                  </button>
                  <a class="btn btn-secondary btn-sm" data-gamepad-control
                    :href="downloadUrl(session.id, part)" :download="`${session.id}_${part.file}`">
                    <Download :size="15" aria-hidden="true" />
                    Save
                  </a>
                </span>
              </li>
            </ul>
          </div>

          <div class="recording-detail-actions">
            <button v-if="!isActive(session)" type="button" class="btn btn-sm"
              :class="confirmingDelete === session.id ? 'btn-danger' : 'btn-secondary'"
              data-gamepad-control
              @click="confirmingDelete === session.id ? confirmDelete(session.id) : askDelete(session.id)">
              <Trash2 :size="15" aria-hidden="true" />
              {{ confirmingDelete === session.id ? 'Delete for good' : 'Delete' }}
            </button>
            <button v-if="confirmingDelete === session.id" type="button"
              class="btn btn-secondary btn-sm" data-gamepad-control @click="askDelete(session.id)">
              Keep
            </button>
            <p v-if="isActive(session)" class="recording-note">
              This recording is still running. Stop it before deleting it.
            </p>
          </div>
        </div>
      </li>
    </ul>

    <div v-if="playing" class="recording-player-backdrop" role="presentation"
      @pointerdown.self="closePlayer">
      <section class="recording-player" role="dialog" aria-modal="true"
        aria-labelledby="recording-player-title">
        <header class="recording-player-header">
          <h2 id="recording-player-title">
            <FileVideo :size="17" aria-hidden="true" />
            {{ playing.sourceId }}
            <span class="recording-player-file">{{ playing.file }}</span>
          </h2>
          <button type="button" class="drawer-close-button" data-gamepad-control
            title="Close the player" aria-label="Close the player" @click="closePlayer">
            <X :size="20" aria-hidden="true" />
          </button>
        </header>

        <video class="recording-video" :src="playingSource" autoplay controls playsinline></video>

        <footer class="recording-player-footer">
          <p v-if="playing.transcode" class="recording-note">
            This camera recorded a format the player cannot decode, so it is being
            converted as it plays.
          </p>
          <label v-if="playing.duration" class="recording-seek">
            <span>Start from {{ formatDuration(playing.offset) }}</span>
            <input type="range" min="0" :max="Math.floor(playing.duration)" step="1"
              :value="playing.offset" data-gamepad-control @change="seekTo" />
          </label>
          <p class="recording-note recording-note-quiet">
            The clip is converted as it plays, so the scrub bar only covers what has
            loaded. Use the slider to start somewhere else.
          </p>
        </footer>
      </section>
    </div>
  </div>
</template>
