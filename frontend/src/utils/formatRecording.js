// Formatting for the recordings page. Kept out of the view so the shapes the
// backend can legitimately return -- an unknown duration, a session whose
// start time could not be parsed -- are handled in one place rather than
// repeated in six templates.

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

export function formatBytes(bytes) {
    if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return '--'
    if (bytes < 1000) return `${bytes} B`
    let value = bytes
    let unit = 0
    while (value >= 1000 && unit < BYTE_UNITS.length - 1) {
        value /= 1000
        unit += 1
    }
    return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${BYTE_UNITS[unit]}`
}

// h:mm:ss, dropping the hour when there is none. An unknown duration is a dash
// rather than 0:00: a scanned folder genuinely does not know, and showing zero
// would read as an empty recording.
export function formatDuration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '--'
    const whole = Math.round(seconds)
    const hours = Math.floor(whole / 3600)
    const minutes = Math.floor((whole % 3600) / 60)
    const rest = whole % 60
    const padded = `${String(minutes).padStart(hours ? 2 : 1, '0')}:${String(rest).padStart(2, '0')}`
    return hours ? `${hours}:${padded}` : padded
}

function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

// "Today 14:05" for the recordings an operator is most likely looking for,
// a full date for the rest.
export function formatSessionTime(startedAt, sessionId) {
    const date = sessionDate(startedAt, sessionId)
    if (date === null) return sessionId ?? 'Unknown time'

    const clock = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    const days = Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000)
    if (days === 0) return `Today ${clock}`
    if (days === 1) return `Yesterday ${clock}`
    return `${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })} ${clock}`
}

export function formatClockTime(startedAt, sessionId) {
    const date = sessionDate(startedAt, sessionId)
    if (date === null) return ''
    return date.toLocaleString()
}

// The backend sends started_at as epoch seconds, but a folder whose name could
// not be parsed has none. The directory name is the same information in a
// different form, so fall back to it rather than showing nothing.
function sessionDate(startedAt, sessionId) {
    if (typeof startedAt === 'number' && Number.isFinite(startedAt)) {
        return new Date(startedAt * 1000)
    }
    const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(sessionId ?? '')
    if (match === null) return null
    const [, year, month, day, hour, minute, second] = match.map(Number)
    const date = new Date(year, month - 1, day, hour, minute, second)
    return Number.isNaN(date.getTime()) ? null : date
}

const STATUS_LABELS = {
    recording: 'Recording',
    complete: 'Complete',
    interrupted: 'Interrupted',
    scanned: 'Filenames only',
}

export function describeStatus(status) {
    return STATUS_LABELS[status] ?? 'Unknown'
}

const STOPPED_REASONS = {
    operator: 'Stopped by the operator',
    low_disk: 'Stopped: the card ran out of space',
    folder_lost: 'Stopped: the card was removed',
    shutdown: 'Stopped: the backend shut down',
}

export function describeStoppedReason(reason) {
    return STOPPED_REASONS[reason] ?? null
}

// What a source's state means for the page's central question: did this camera
// produce video, and if not, why not.
export function describeSource(source) {
    if (source.recorded) {
        const parts = source.part_count === 1 ? '1 file' : `${source.part_count} files`
        return `${parts}, ${formatBytes(source.bytes)}`
    }
    if (source.error) return source.error
    if (source.status === 'failed') return 'Recorded nothing'
    return 'No video'
}
