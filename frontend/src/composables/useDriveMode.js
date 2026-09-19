import { computed, readonly, ref } from 'vue'

// Which way the left stick drives. The stick has no lower half -- it travels
// up from centre and nowhere else -- so the robot's direction is a mode the
// operator selects rather than a sign they hold: 'forward' sends that travel
// as positive Y velocity, 'reverse' sends the same travel negated.
//
// Deliberately not persisted. Reverse is the mode that surprises, and a Deck
// that woke up already in it would surprise worst of all, so every start is
// forward.
const DRIVE_MODES = ['forward', 'reverse']

const driveMode = ref(DRIVE_MODES[0])

const reversing = computed(() => driveMode.value === 'reverse')

function toggleDriveMode() {
    driveMode.value = reversing.value ? 'forward' : 'reverse'
}

export function useDriveMode() {
    return {
        driveMode: readonly(driveMode),
        reversing,
        toggleDriveMode,
    }
}
