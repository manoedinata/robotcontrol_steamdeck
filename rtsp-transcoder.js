const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const http = require('node:http')
const ffmpegPath = require('ffmpeg-static')

const MAX_FRAME_BYTES = 16 * 1024 * 1024
const JPEG_START = Buffer.from([0xff, 0xd8])
const JPEG_END = Buffer.from([0xff, 0xd9])

class RtspTranscoder {
    constructor() {
        this.sourceUrl = ''
        this.token = ''
        this.server = null
        this.process = null
        this.clients = new Set()
        this.frameBuffer = Buffer.alloc(0)
        this.operation = Promise.resolve()
    }

    async resolveStreamUrl(cameraUrl) {
        const operation = this.operation.then(() => this.resolveStreamUrlNow(cameraUrl))
        this.operation = operation.catch(() => { })
        return operation
    }

    async resolveStreamUrlNow(cameraUrl) {
        const sourceUrl = typeof cameraUrl === 'string' ? cameraUrl.trim() : ''

        if (!sourceUrl) {
            this.stop()
            return ''
        }

        let parsedUrl
        try {
            parsedUrl = new URL(sourceUrl)
        } catch {
            throw new Error('Camera URL is invalid.')
        }

        if (!['http:', 'https:', 'rtsp:'].includes(parsedUrl.protocol)) {
            throw new Error('Camera URL must use HTTP, HTTPS, or RTSP.')
        }

        if (parsedUrl.protocol !== 'rtsp:') {
            this.stop()
            return sourceUrl
        }

        if (this.sourceUrl === sourceUrl && this.server?.listening) {
            return this.getRelayUrl()
        }

        await this.start(sourceUrl)
        return this.getRelayUrl()
    }

    async start(sourceUrl) {
        this.stop()
        this.sourceUrl = sourceUrl
        this.token = crypto.randomBytes(24).toString('hex')

        this.server = http.createServer((request, response) => {
            if (request.method !== 'GET' || request.url !== `/stream/${this.token}`) {
                response.writeHead(404).end()
                return
            }

            response.writeHead(200, {
                'Cache-Control': 'no-store, no-cache, must-revalidate',
                Connection: 'close',
                'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                Expires: '0',
                Pragma: 'no-cache',
            })
            this.clients.add(response)
            request.on('close', () => this.clients.delete(response))
        })

        await new Promise((resolve, reject) => {
            const handleError = (error) => {
                this.server = null
                reject(error)
            }

            this.server.once('error', handleError)
            this.server.listen(0, '127.0.0.1', () => {
                this.server.off('error', handleError)
                resolve()
            })
        })

        this.spawnFfmpeg(sourceUrl)
        console.info('[camera] RTSP transcoder started')
    }

    spawnFfmpeg(sourceUrl) {
        const args = [
            '-hide_banner',
            '-loglevel', 'warning',
            '-rtsp_transport', 'tcp',
            '-i', sourceUrl,
            '-an',
            '-vf', 'scale=1280:800:force_original_aspect_ratio=decrease',
            '-r', '15',
            '-c:v', 'mjpeg',
            '-q:v', '5',
            '-f', 'image2pipe',
            'pipe:1',
        ]

        const child = spawn(ffmpegPath, args, {
            stdio: ['ignore', 'pipe', 'pipe'],
            windowsHide: true,
        })
        this.process = child

        child.stdout.on('data', (chunk) => {
            if (this.process === child) this.consumeFrames(chunk)
        })
        child.stderr.on('data', (chunk) => {
            const message = chunk.toString().trim().replaceAll(sourceUrl, '[camera-url-redacted]')
            if (message) {
                console.warn('[camera] RTSP transcoder:', message)
            }
        })
        child.once('error', (error) => {
            console.error('[camera] Could not start RTSP transcoder:', error.message)
            if (this.process === child) {
                this.process = null
                this.closeClients()
                this.server?.close()
                this.server = null
                this.sourceUrl = ''
                this.token = ''
            }
        })
        child.once('exit', (code, signal) => {
            if (this.process === child) {
                console.warn('[camera] RTSP transcoder stopped', { code, signal })
                this.process = null
                this.closeClients()
                this.server?.close()
                this.server = null
                this.sourceUrl = ''
                this.token = ''
            }
        })
    }

    consumeFrames(chunk) {
        this.frameBuffer = Buffer.concat([this.frameBuffer, chunk])

        while (this.frameBuffer.length) {
            const start = this.frameBuffer.indexOf(JPEG_START)
            if (start === -1) {
                this.frameBuffer = this.frameBuffer.subarray(Math.max(0, this.frameBuffer.length - 1))
                return
            }

            const end = this.frameBuffer.indexOf(JPEG_END, start + JPEG_START.length)
            if (end === -1) {
                this.frameBuffer = this.frameBuffer.subarray(start)
                if (this.frameBuffer.length > MAX_FRAME_BYTES) {
                    console.warn('[camera] Discarding oversized transcoder frame')
                    this.frameBuffer = Buffer.alloc(0)
                }
                return
            }

            const frame = this.frameBuffer.subarray(start, end + JPEG_END.length)
            this.frameBuffer = this.frameBuffer.subarray(end + JPEG_END.length)
            this.broadcastFrame(frame)
        }
    }

    broadcastFrame(frame) {
        const header = Buffer.from(
            `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`,
        )

        for (const client of this.clients) {
            if (!client.destroyed && !client.writableNeedDrain) {
                client.write(header)
                client.write(frame)
                client.write('\r\n')
            }
        }
    }

    getRelayUrl() {
        const address = this.server?.address()
        if (!address || typeof address === 'string') {
            throw new Error('RTSP relay is unavailable.')
        }
        return `http://127.0.0.1:${address.port}/stream/${this.token}`
    }

    closeClients() {
        for (const client of this.clients) {
            client.destroy()
        }
        this.clients.clear()
    }

    stop() {
        const process = this.process
        this.process = null
        if (process && !process.killed) {
            process.kill('SIGTERM')
        }

        this.closeClients()
        this.server?.close()
        this.server = null
        this.sourceUrl = ''
        this.token = ''
        this.frameBuffer = Buffer.alloc(0)
    }
}

module.exports = { RtspTranscoder }