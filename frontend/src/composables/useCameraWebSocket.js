const DEFAULT_CODEC = 'avc1.42E01E'
const MAX_DECODE_QUEUE_SIZE = 2

function findNalTypes(bytes) {
    const types = []
    for (let index = 0; index + 4 < bytes.length; index += 1) {
        const startCodeLength = bytes[index] === 0 && bytes[index + 1] === 0
            ? (bytes[index + 2] === 1 ? 3 : bytes[index + 2] === 0 && bytes[index + 3] === 1 ? 4 : 0)
            : 0
        if (startCodeLength) {
            types.push(bytes[index + startCodeLength] & 0x1f)
            index += startCodeLength
        }
    }
    return types
}

function isKeyFrame(bytes) {
    return findNalTypes(bytes).includes(5)
}

function createTimestamp() {
    let timestamp = 0
    return () => {
        timestamp += 1
        return timestamp
    }
}

export function useCameraWebSocket(canvasElement, onStateChange) {
    let socket = null
    let decoder = null
    let renderContext = null
    let nextTimestamp = createTimestamp()
    let closed = false
    let connectionGeneration = 0
    let waitingForKeyFrame = false
    let pendingKeyFrame = null

    function report(state, error = null) {
        onStateChange(state, error)
    }

    function close() {
        connectionGeneration += 1
        closed = true
        socket?.close()
        socket = null
        decoder?.close()
        decoder = null
        renderContext = null
        waitingForKeyFrame = false
        pendingKeyFrame = null
    }

    async function connect(url) {
        close()
        const generation = connectionGeneration
        closed = false
        waitingForKeyFrame = false
        pendingKeyFrame = null
        if (!url) {
            report('idle')
            return
        }
        if (!globalThis.VideoDecoder || !globalThis.EncodedVideoChunk) {
            throw new Error('WebCodecs H.264 decoding is unavailable in this Electron build.')
        }
        if (!canvasElement.value) throw new Error('Camera canvas is unavailable.')

        renderContext = canvasElement.value.getContext('2d', { alpha: false })
        if (!renderContext) throw new Error('Camera canvas could not be initialized.')
        report('loading')

        function submitFrame(bytes, keyFrame) {
            if (closed || !decoder || decoder.state === 'closed') return false
            if (decoder.decodeQueueSize >= MAX_DECODE_QUEUE_SIZE) return false

            decoder.decode(new EncodedVideoChunk({
                type: keyFrame ? 'key' : 'delta',
                timestamp: nextTimestamp(),
                data: bytes,
            }))
            return true
        }

        function submitPendingKeyFrame() {
            if (!pendingKeyFrame || !decoder || decoder.decodeQueueSize >= MAX_DECODE_QUEUE_SIZE) return
            const bytes = pendingKeyFrame
            pendingKeyFrame = null
            try {
                if (submitFrame(bytes, true)) waitingForKeyFrame = false
                else pendingKeyFrame = bytes
            } catch (error) {
                report('error', error.message || 'Camera keyframe could not be decoded.')
            }
        }

        decoder = new VideoDecoder({
            output: (frame) => {
                if (closed || !canvasElement.value) {
                    frame.close()
                    return
                }
                if (canvasElement.value.width !== frame.displayWidth || canvasElement.value.height !== frame.displayHeight) {
                    canvasElement.value.width = frame.displayWidth
                    canvasElement.value.height = frame.displayHeight
                }
                renderContext.drawImage(frame, 0, 0, canvasElement.value.width, canvasElement.value.height)
                frame.close()
                report('connected')
                queueMicrotask(submitPendingKeyFrame)
            },
            error: (error) => {
                if (!closed) report('error', error.message || 'H.264 decoder failed.')
            },
        })

        const support = await VideoDecoder.isConfigSupported({ codec: DEFAULT_CODEC })
        if (generation !== connectionGeneration || closed) return
        if (!support.supported) throw new Error(`H.264 codec ${DEFAULT_CODEC} is not supported.`)
        decoder.configure({ ...support.config, optimizeForLatency: true })

        await new Promise((resolve, reject) => {
            const nextSocket = new WebSocket(url)
            if (generation !== connectionGeneration || closed) {
                nextSocket.close()
                resolve()
                return
            }
            socket = nextSocket
            nextSocket.binaryType = 'arraybuffer'
            nextSocket.addEventListener('open', () => {
                if (socket !== nextSocket || closed) return
                nextSocket.send('PlayStream2')
                resolve()
            }, { once: true })
            nextSocket.addEventListener('message', (event) => {
                if (socket !== nextSocket || closed || typeof event.data === 'string') return
                const bytes = new Uint8Array(event.data)
                if (!bytes.length || decoder?.state === 'closed') return
                const keyFrame = isKeyFrame(bytes)

                if (waitingForKeyFrame) {
                    if (keyFrame) pendingKeyFrame = bytes
                    submitPendingKeyFrame()
                    return
                }

                if (decoder.decodeQueueSize >= MAX_DECODE_QUEUE_SIZE) {
                    waitingForKeyFrame = true
                    if (keyFrame) pendingKeyFrame = bytes
                    return
                }

                try {
                    submitFrame(bytes, keyFrame)
                } catch (error) {
                    report('error', error.message || 'Camera frame could not be decoded.')
                }
            })
            nextSocket.addEventListener('error', () => reject(new Error('Camera WebSocket connection failed.')), { once: true })
            nextSocket.addEventListener('close', () => {
                if (socket === nextSocket && !closed) report('error', 'Camera WebSocket connection closed.')
            })
        })
    }

    return { connect, close }
}
