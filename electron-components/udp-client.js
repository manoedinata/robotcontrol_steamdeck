const { createSocket } = require('node:dgram')
const path = require('node:path')
const { loadPacketCodec } = require('./packet-schema')

const DEFAULT_SCHEMA_PATH = path.join(__dirname, '..', 'packet-schema.json')

class UdpClient {
    // A schema path (or a pre-built PacketCodec) drives the on-wire layout so
    // the command and battery packets stay JSON-configurable.
    constructor(options = {}) {
        this.codec = options.codec ?? loadPacketCodec(options.schemaPath ?? DEFAULT_SCHEMA_PATH)
        this.batteryFieldName = this.codec.fieldNameForRole('reply', 'batteryPercentage')
        this.client = createSocket('udp4')
        this.batteryListeners = new Set()
        this.client.on('message', (message, remoteInfo) => this.handleMessage(message, remoteInfo))
        this.client.on('error', (error) => {
            console.error('UDP client socket error:', error)
        })
    }

    handleMessage(message, remoteInfo) {
        const reply = this.codec.decodeReply(message)
        if (!reply) return

        const batteryPercentage = this.batteryFieldName
            ? reply[this.batteryFieldName]
            : undefined

        for (const listener of this.batteryListeners) {
            listener(batteryPercentage, remoteInfo, reply)
        }
    }

    onBatteryPercentage(listener) {
        this.batteryListeners.add(listener)
        return () => this.batteryListeners.delete(listener)
    }

    verifyDestination(host, port) {
        if (
            typeof host !== 'string'
            || host.trim().length === 0
            || !Number.isInteger(port)
            || port < 1
            || port > 65535
        ) {
            throw new TypeError('Invalid arguments for sending UDP message.')
        }
    }

    // Serializes a { fieldName: value } command map (e.g. { yVelocity,
    // thetaVelocity }) according to the active schema and sends it.
    sendCommand(host, port, values) {
        this.verifyDestination(host, port)

        const packet = this.codec.encodeCommand(values)

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
        this.batteryListeners.clear()
        this.client.close()
    }
}

module.exports = { UdpClient }