// Shared, JSON-driven UDP packet codec used by both the Electron main process
// (electron-components/udp-client.js) and the development diagnostic peer
// (udp-server.js). A single schema describes the on-wire layout of the command
// and reply packets so the sender and receiver can never disagree about byte
// offsets, field sizes, byte order, or which named fields exist.
//
// The schema is intentionally declarative: adding a field, changing its size,
// or reordering the layout is a JSON edit, not a code change. Offsets are
// derived from field order, so authors never hand-maintain byte positions.

const fs = require('node:fs')

const MAX_FLOAT32 = 3.4028234663852886e38
const HEADER_PATTERN = /^[\x00-\x7f]+$/

// Supported numeric field types. Each entry declares its byte width and the
// Buffer accessor suffix used to build little/big-endian read/write methods.
const FIELD_TYPES = {
    float32: { size: 4, read: 'readFloat', write: 'writeFloat', kind: 'float' },
    float64: { size: 8, read: 'readDouble', write: 'writeDouble', kind: 'float' },
    int8: { size: 1, read: 'readInt8', write: 'writeInt8', kind: 'int', endian: false },
    uint8: { size: 1, read: 'readUInt8', write: 'writeUInt8', kind: 'int', endian: false },
    int16: { size: 2, read: 'readInt16', write: 'writeInt16', kind: 'int' },
    uint16: { size: 2, read: 'readUInt16', write: 'writeUInt16', kind: 'int' },
    int32: { size: 4, read: 'readInt32', write: 'writeInt32', kind: 'int' },
    uint32: { size: 4, read: 'readUInt32', write: 'writeUInt32', kind: 'int' },
}

const INT_RANGES = {
    int8: [-128, 127],
    uint8: [0, 255],
    int16: [-32768, 32767],
    uint16: [0, 65535],
    int32: [-2147483648, 2147483647],
    uint32: [0, 4294967295],
}

// Built-in fallback layout. Matches the historical 11-byte command / 7-byte
// battery reply protocol so existing deployments keep working when no schema
// file is present.
const DEFAULT_SCHEMA = {
    byteOrder: 'little',
    command: {
        header: 'ITS',
        fields: [
            { name: 'yVelocity', type: 'float32', role: 'yVelocity', default: 0 },
            { name: 'thetaVelocity', type: 'float32', role: 'thetaVelocity', default: 0 },
        ],
    },
    reply: {
        header: 'ITS',
        fields: [
            { name: 'batteryPercentage', type: 'int32', role: 'batteryPercentage', min: 0, max: 100, default: 0 },
        ],
    },
}

function methodSuffix(endian) {
    return endian === 'big' ? 'BE' : 'LE'
}

// Validates one packet definition (command or reply) and returns a compiled
// descriptor with resolved byte offsets, total size, and per-type accessors.
function compilePacket(definition, byteOrder, label) {
    if (!definition || typeof definition !== 'object') {
        throw new TypeError(`Packet schema "${label}" must be an object.`)
    }

    const header = definition.header
    if (typeof header !== 'string' || !HEADER_PATTERN.test(header)) {
        throw new TypeError(`Packet schema "${label}" header must be a non-empty ASCII string.`)
    }

    if (!Array.isArray(definition.fields) || definition.fields.length === 0) {
        throw new TypeError(`Packet schema "${label}" must declare at least one field.`)
    }

    const headerSize = Buffer.byteLength(header, 'ascii')
    const suffix = methodSuffix(byteOrder)
    const seenNames = new Set()
    let offset = headerSize

    const fields = definition.fields.map((field, index) => {
        if (!field || typeof field !== 'object') {
            throw new TypeError(`Packet schema "${label}" field ${index} must be an object.`)
        }
        const { name, type } = field
        if (typeof name !== 'string' || name.trim().length === 0) {
            throw new TypeError(`Packet schema "${label}" field ${index} needs a non-empty name.`)
        }
        if (seenNames.has(name)) {
            throw new TypeError(`Packet schema "${label}" has a duplicate field name "${name}".`)
        }
        seenNames.add(name)

        const typeInfo = FIELD_TYPES[type]
        if (!typeInfo) {
            throw new TypeError(
                `Packet schema "${label}" field "${name}" has unsupported type "${type}". `
                + `Supported types: ${Object.keys(FIELD_TYPES).join(', ')}.`,
            )
        }

        // Single-byte integers have no endianness; multi-byte values use the suffix.
        const endianSuffix = typeInfo.endian === false ? '' : suffix
        const compiled = {
            name,
            type,
            role: typeof field.role === 'string' ? field.role : name,
            offset,
            size: typeInfo.size,
            kind: typeInfo.kind,
            readMethod: `${typeInfo.read}${endianSuffix}`,
            writeMethod: `${typeInfo.write}${endianSuffix}`,
            default: Number.isFinite(field.default) ? field.default : 0,
            min: Number.isFinite(field.min) ? field.min : undefined,
            max: Number.isFinite(field.max) ? field.max : undefined,
        }
        offset += typeInfo.size
        return compiled
    })

    return {
        header,
        headerSize,
        size: offset,
        fields,
        // Fast lookup of a field by its semantic role (e.g. "batteryPercentage").
        fieldByRole: new Map(fields.map((field) => [field.role, field])),
    }
}

// Ensures a value is representable by the field's type before it is written.
function assertWritable(field, value) {
    if (!Number.isFinite(value)) {
        throw new RangeError(`Field "${field.name}" requires a finite number.`)
    }
    if (field.kind === 'float') {
        if (field.type === 'float32' && Math.abs(value) > MAX_FLOAT32) {
            throw new RangeError(`Field "${field.name}" exceeds the float32 range.`)
        }
        return
    }
    if (!Number.isInteger(value)) {
        throw new RangeError(`Field "${field.name}" requires an integer value.`)
    }
    const [low, high] = INT_RANGES[field.type]
    if (value < low || value > high) {
        throw new RangeError(`Field "${field.name}" is outside the ${field.type} range.`)
    }
}

class PacketCodec {
    constructor(schema) {
        const byteOrder = schema?.byteOrder === 'big' ? 'big' : 'little'
        this.byteOrder = byteOrder
        this.command = compilePacket(schema?.command, byteOrder, 'command')
        this.reply = compilePacket(schema?.reply, byteOrder, 'reply')
    }

    // Serializes a { fieldName: value } map into a Buffer for the given packet.
    // Missing fields fall back to their declared default.
    encode(packet, values = {}) {
        const buffer = Buffer.allocUnsafe(packet.size)
        buffer.write(packet.header, 0, packet.headerSize, 'ascii')
        for (const field of packet.fields) {
            const provided = values[field.name]
            const value = provided === undefined ? field.default : provided
            assertWritable(field, value)
            buffer[field.writeMethod](value, field.offset)
        }
        return buffer
    }

    // Parses a Buffer into a { fieldName: value } map, or returns null when the
    // length, header, or a declared min/max bound does not match the schema.
    decode(packet, message) {
        if (!Buffer.isBuffer(message) || message.length !== packet.size) {
            return null
        }
        if (message.toString('ascii', 0, packet.headerSize) !== packet.header) {
            return null
        }
        const values = {}
        for (const field of packet.fields) {
            const value = message[field.readMethod](field.offset)
            if (field.min !== undefined && value < field.min) return null
            if (field.max !== undefined && value > field.max) return null
            values[field.name] = value
        }
        return values
    }

    encodeCommand(values) {
        return this.encode(this.command, values)
    }

    decodeCommand(message) {
        return this.decode(this.command, message)
    }

    encodeReply(values) {
        return this.encode(this.reply, values)
    }

    decodeReply(message) {
        return this.decode(this.reply, message)
    }

    // Resolves a field name from its semantic role for a given packet ("command"
    // or "reply"), so callers can locate well-known values (yVelocity, battery)
    // without hard-coding field names.
    fieldNameForRole(kind, role) {
        const packet = kind === 'reply' ? this.reply : this.command
        return packet.fieldByRole.get(role)?.name
    }
}

// Loads and compiles a schema from a JSON file. Falls back to the built-in
// default layout when the file is missing; malformed JSON or invalid schemas
// throw so misconfiguration surfaces immediately instead of silently sending
// wrong-shaped packets.
function loadPacketCodec(schemaPath) {
    let schema = DEFAULT_SCHEMA
    if (schemaPath) {
        try {
            schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'))
        } catch (error) {
            if (error.code === 'ENOENT') {
                schema = DEFAULT_SCHEMA
            } else {
                throw new Error(`Failed to load UDP packet schema at ${schemaPath}: ${error.message}`)
            }
        }
    }
    return new PacketCodec(schema)
}

module.exports = {
    PacketCodec,
    loadPacketCodec,
    DEFAULT_SCHEMA,
    FIELD_TYPES,
}
