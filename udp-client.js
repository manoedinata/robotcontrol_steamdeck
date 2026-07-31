const { createSocket } = require('node:dgram')

const HEADER_SIZE = 3
const VELOCITY_COUNT = 2
const PACKET_SIZE = HEADER_SIZE + (VELOCITY_COUNT * Int32Array.BYTES_PER_ELEMENT)

function packVelocityPacket(header, velocity) {
    if (typeof header !== 'string' || !/^[\x00-\x7f]{3}$/.test(header)) {
        throw new TypeError('UDP packet header must contain exactly 3 ASCII characters.')
    }
    if (!Array.isArray(velocity) || velocity.length !== VELOCITY_COUNT) {
        throw new TypeError('UDP packet velocity must be an array containing Y and theta.')
    }
    if (!velocity.every((value) => Number.isInteger(value) && value >= -0x80000000 && value <= 0x7fffffff)) {
        throw new RangeError('UDP packet velocities must be signed 32-bit integers.')
    }

    const packet = Buffer.allocUnsafe(PACKET_SIZE)
    packet.write(header, 0, HEADER_SIZE, 'ascii')
    packet.writeInt32LE(velocity[0], HEADER_SIZE)
    packet.writeInt32LE(velocity[1], HEADER_SIZE + Int32Array.BYTES_PER_ELEMENT)
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

module.exports = { UdpClient }