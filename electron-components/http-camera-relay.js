const crypto = require('node:crypto')
const http = require('node:http')
const https = require('node:https')

const MAX_REDIRECTS = 5

class HttpCameraRelay {
    constructor(onFrame) {
        this.onFrame = onFrame
        this.sourceUrl = ''
        this.token = ''
        this.server = null
        this.upstreamRequests = new Set()
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

        if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
            throw new Error('HTTP camera relay requires an HTTP or HTTPS URL.')
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

            this.proxySource(sourceUrl, response)
            request.on('close', () => response.destroy())
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
    }

    proxySource(sourceUrl, response, redirectCount = 0) {
        const transport = sourceUrl.startsWith('https:') ? https : http
        const upstreamRequest = transport.get(sourceUrl, {
            headers: { Accept: 'multipart/x-mixed-replace,image/jpeg,image/*' },
        })
        this.upstreamRequests.add(upstreamRequest)

        upstreamRequest.once('response', (upstreamResponse) => {
            const statusCode = upstreamResponse.statusCode || 502
            const redirectUrl = upstreamResponse.headers.location

            if (statusCode >= 300 && statusCode < 400 && redirectUrl) {
                upstreamResponse.resume()
                this.upstreamRequests.delete(upstreamRequest)
                if (redirectCount >= MAX_REDIRECTS) {
                    response.writeHead(502).end()
                    return
                }
                const nextUrl = new URL(redirectUrl, sourceUrl)
                if (!['http:', 'https:'].includes(nextUrl.protocol)) {
                    response.writeHead(502).end()
                    return
                }
                this.proxySource(nextUrl.href, response, redirectCount + 1)
                return
            }

            if (statusCode < 200 || statusCode >= 300) {
                upstreamResponse.resume()
                response.writeHead(statusCode).end()
                return
            }

            response.writeHead(200, {
                'Cache-Control': 'no-store, no-cache, must-revalidate',
                Connection: 'close',
                'Content-Type': upstreamResponse.headers['content-type'] || 'image/jpeg',
                Expires: '0',
                Pragma: 'no-cache',
            })

            let previousByte = -1
            let insideFrame = false
            upstreamResponse.on('data', (chunk) => {
                for (const byte of chunk) {
                    if (!insideFrame && previousByte === 0xff && byte === 0xd8) {
                        insideFrame = true
                    } else if (insideFrame && previousByte === 0xff && byte === 0xd9) {
                        insideFrame = false
                        this.onFrame?.()
                    }
                    previousByte = byte
                }
            })
            upstreamResponse.pipe(response)
            upstreamResponse.once('error', () => response.destroy())
        })

        upstreamRequest.once('close', () => this.upstreamRequests.delete(upstreamRequest))
        upstreamRequest.once('error', () => {
            if (!response.headersSent) response.writeHead(502)
            response.end()
        })
        response.once('close', () => upstreamRequest.destroy())
    }

    getRelayUrl() {
        const address = this.server?.address()
        if (!address || typeof address === 'string') {
            throw new Error('HTTP camera relay is unavailable.')
        }
        return `http://127.0.0.1:${address.port}/stream/${this.token}`
    }

    stop() {
        for (const request of this.upstreamRequests) request.destroy()
        this.upstreamRequests.clear()
        this.server?.close()
        this.server = null
        this.sourceUrl = ''
        this.token = ''
    }
}

module.exports = { HttpCameraRelay }