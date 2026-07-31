const { createSocket } = require('node:dgram')

const HEADER_SIZE = 3
const VELOCITY_COUNT = 2
const PACKET_SIZE = HEADER_SIZE + (VELOCITY_COUNT * Float32Array.BYTES_PER_ELEMENT)
const MAX_FLOAT32 = 3.4028234663852886e38

function packVelocityPacket(header, velocity) {
    if (typeof header !== 'string' || !/^[\x00-\x7f]{3}$/.test(header)) {
        throw new TypeError('UDP packet header must contain exactly 3 ASCII characters.')
    }
    if (!Array.isArray(velocity) || velocity.length !== VELOCITY_COUNT) {
        throw new TypeError('UDP packet velocity must be an array containing Y and theta.')
    }
    if (!velocity.every((value) => Number.isFinite(value) && Math.abs(value) <= MAX_FLOAT32)) {
        throw new RangeError('UDP packet velocities must be finite 32-bit floating-point values.')
    }

    const packet = Buffer.allocUnsafe(PACKET_SIZE)
    packet.write(header, 0, HEADER_SIZE, 'ascii')
    packet.writeFloatLE(velocity[0], HEADER_SIZE)
    packet.writeFloatLE(velocity[1], HEADER_SIZE + Float32Array.BYTES_PER_ELEMENT)
    return packet
}

class UdpClient {
    constructor() {
        this.client = createSocket('udp4')
    }

    sendMessage(host, port, header, velocity) {
        const packet = packVelocityPacket(header, velocity)

        return new Promise((resolve, reject) => {
            this.client.send(packet, port, host, (error) => {
                if (error) {
                    reject(error)
                    return
                }

                resolve()
            })
        })
    }

    close() {
        this.client.close()
    }
}

module.exports = { UdpClient, packVelocityPacket }