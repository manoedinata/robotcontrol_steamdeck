import { computed, readonly, ref, watch } from 'vue'
import { useBackendConnection } from './useBackendConnection'
import { useSettings } from './useSettings'

// Differential-drive odometry over the robot's two wheel encoders.
//
// Frames 1 and 2 of the telemetry packet are absolute encoder positions, so
// every step works on the difference between one packet and the last:
//
//   d_left       = (left  - prev_left)  * metres per count
//   d_right      = (right - prev_right) * metres per count
//   delta_theta  = (d_right - d_left) / wheel separation
//   delta_s      = (d_right + d_left) / 2
//   x     += delta_s * cos(theta + delta_theta / 2)
//   y     += delta_s * sin(theta + delta_theta / 2)
//   theta += delta_theta
//
// The half-step heading is what makes a turn an arc rather than a corner: over
// one packet the robot is somewhere between the heading it started with and
// the one it ends with, and the midpoint is the closest a straight segment
// gets to the curve it is standing in for.
//
// This lives in the renderer because the wire carries counts and nothing else:
// what a count is worth, and how far apart the wheels are, are the operator's
// measurements of their own robot, not something the robot reports.
const x = ref(0)
const y = ref(0)
const theta = ref(0)
// Signed: driving forward adds, reversing subtracts, so this is travel made
// good rather than an odometer that only ever climbs.
const travelled = ref(0)

let previous = null
let lastLogAt = 0
// The counts this run started from. Reactive, unlike `previous`, because the
// debug readout shows counts measured against it: after a reset both wheels
// read from zero again, so the strip stays comparable with the distance beside
// it. The robot's own absolute counts are never touched -- only what is
// subtracted from them here.
const origin = ref(null)

const { telemetry } = useBackendConnection()
const { distancePerCount, wheelSeparation } = useSettings()

function reset() {
    x.value = 0
    y.value = 0
    theta.value = 0
    travelled.value = 0
    previous = null
    origin.value = null
}

function integrate(packet) {
    // The socket went down and the readings that follow cannot be assumed to
    // continue the ones before them, so the next packet starts a new baseline
    // instead of being differenced against a stale one.
    if (!packet) {
        previous = null
        return
    }

    const left = packet.position_left
    const right = packet.position_right
    if (typeof left !== 'number' || typeof right !== 'number') return

    // The first reading is a starting point, not a movement.
    if (previous === null) {
        previous = { left, right }
        if (origin.value === null) origin.value = { left, right }
        return
    }

    const deltaLeft = left - previous.left
    const deltaRight = right - previous.right
    previous = { left, right }

    // A scale changed mid-drive applies from here on and does not rewrite the
    // distance already travelled: the metres behind the robot were measured
    // under the old figure and are not re-measurable.
    const dLeft = deltaLeft * distancePerCount.value
    const dRight = deltaRight * distancePerCount.value

    // Without a wheel separation there is no way to turn a difference between
    // the wheels into an angle, so the robot is treated as driving straight
    // and the distance -- which does not depend on it -- stays right.
    const separation = wheelSeparation.value
    const deltaTheta = separation > 0 ? (dRight - dLeft) / separation : 0
    const deltaS = (dRight + dLeft) / 2

    const heading = theta.value + deltaTheta / 2
    x.value += deltaS * Math.cos(heading)
    y.value += deltaS * Math.sin(heading)
    theta.value += deltaTheta
    travelled.value += deltaS

    // Logged while what else to do with the pose is being decided.
    const now = Date.now()
    if (now - lastLogAt >= 1000) {
        lastLogAt = now
        console.log(
            '[odometry] x=%sm y=%sm theta=%s° travelled=%sm',
            x.value.toFixed(3),
            y.value.toFixed(3),
            headingDegrees.value.toFixed(1),
            travelled.value.toFixed(3),
        )
    }
}

watch(telemetry, integrate)

// Unbounded while it is integrated -- wrapping mid-sum would cost a turn's
// worth of angle -- and wrapped into -180..180 only to be read.
const headingDegrees = computed(() => {
    const wrapped = ((theta.value * 180) / Math.PI + 180) % 360
    return (wrapped < 0 ? wrapped + 360 : wrapped) - 180
})

// Counts since the run started, which is what the reset zeroes. Null until a
// first reading has arrived to measure against.
const countsLeft = computed(() => {
    const left = telemetry.value?.position_left
    if (typeof left !== 'number' || origin.value === null) return null
    return left - origin.value.left
})

const countsRight = computed(() => {
    const right = telemetry.value?.position_right
    if (typeof right !== 'number' || origin.value === null) return null
    return right - origin.value.right
})

export function useOdometry() {
    return {
        countsLeft,
        countsRight,
        x: readonly(x),
        y: readonly(y),
        theta: readonly(theta),
        headingDegrees,
        travelled: readonly(travelled),
        reset,
    }
}
