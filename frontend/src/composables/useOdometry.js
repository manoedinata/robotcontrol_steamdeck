import { computed, readonly, ref, watch } from 'vue'
import { useBackendConnection } from './useBackendConnection'
import { useSettings } from './useSettings'

// Differential-drive odometry over the robot's two wheel speeds.
//
// Frames 3 and 4 of the telemetry packet are the wheel speeds in counts per
// second, already worked out by the robot, so each packet's movement is that
// speed over the time since the last one:
//
//   d_left       = speed_left  * dt * metres per count
//   d_right      = speed_right * dt * metres per count
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
// A speed has to be held over an interval to become a distance, and the only
// interval available here is the gap between arrivals, measured on this side
// of the network. That is the cost of integrating speed rather than
// differencing the positions in frames 1 and 2: those are absolute, so a
// packet lost between two others changes nothing, while a speed missed is
// movement this can never account for. Packets that arrive further apart than
// MAX_STEP_S are treated as a break rather than as the robot having held its
// last speed across the gap, and the positions are still shown beside the
// distance so the two can be checked against each other.
//
// This lives in the renderer because the wire carries counts and nothing else:
// what a count is worth, and how far apart the wheels are, are the operator's
// measurements of their own robot, not something the robot reports.
const MAX_STEP_S = 0.5
const x = ref(0)
const y = ref(0)
const theta = ref(0)
// Signed: driving forward adds, reversing subtracts, so this is travel made
// good rather than an odometer that only ever climbs.
const travelled = ref(0)

// When the last packet arrived, on the monotonic clock. The integration is
// against elapsed time now, not against the previous counts.
let previousAt = null
let lastLogAt = 0

const { telemetry } = useBackendConnection()
const { distancePerCount, wheelSeparation } = useSettings()

function reset() {
    x.value = 0
    y.value = 0
    theta.value = 0
    travelled.value = 0
    previousAt = null
}

function integrate(packet) {
    // The socket went down, and nothing says the robot held its last speed
    // while it was gone, so the next packet starts the clock again.
    if (!packet) {
        previousAt = null
        return
    }

    const speedLeft = packet.speed_left
    const speedRight = packet.speed_right
    if (typeof speedLeft !== 'number' || typeof speedRight !== 'number') return

    const arrivedAt = performance.now()
    // The first packet is a starting instant, not an interval.
    if (previousAt === null) {
        previousAt = arrivedAt
        return
    }

    const elapsed = (arrivedAt - previousAt) / 1000
    previousAt = arrivedAt
    // Too long a gap is a break in the feed, not a long slow step: holding the
    // last speed across it would invent metres the robot may never have moved.
    if (!(elapsed > 0) || elapsed > MAX_STEP_S) return

    // A scale changed mid-drive applies from here on and does not rewrite the
    // distance already travelled: the metres behind the robot were measured
    // under the old figure and are not re-measurable.
    const dLeft = speedLeft * elapsed * distancePerCount.value
    const dRight = speedRight * elapsed * distancePerCount.value

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

export function useOdometry() {
    return {
        x: readonly(x),
        y: readonly(y),
        theta: readonly(theta),
        headingDegrees,
        travelled: readonly(travelled),
        reset,
    }
}
