const { app, BrowserWindow, ipcMain } = require('electron')
const dns = require('node:dns/promises')
const fs = require('node:fs/promises')
const net = require('node:net')
const path = require('node:path')
const { HttpCameraRelay } = require('./electron-components/http-camera-relay')
const { RtspTranscoder } = require('./electron-components/rtsp-transcoder')
const { UdpClient } = require('./electron-components/udp-client')

const settingsPath = path.join(__dirname, 'settings.json')
const UDP_SEND_INTERVAL_MS = 20
const UDP_METRICS_PUBLISH_INTERVAL_MS = 500
const UDP_RX_WINDOW_MS = 1000
const UDP_LOSS_WINDOW_MS = 5000
const UDP_REPLY_GRACE_MS = 250
const UDP_STALE_AFTER_MS = 1000
const CAMERA_FPS_WINDOW_MS = 1000
const CAMERA_FPS_PUBLISH_INTERVAL_MS = 500
let cameraFrameTimestamps = []
const recordCameraFrame = () => cameraFrameTimestamps.push(Date.now())
const publishCameraInterruption = () => {
    cameraFrameTimestamps = []
    for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) {
            window.webContents.send('camera:stream-interrupted')
        }
    }
}
const httpCameraRelay = new HttpCameraRelay(recordCameraFrame, publishCameraInterruption)
const rtspTranscoder = new RtspTranscoder(recordCameraFrame, publishCameraInterruption)
const udpClient = new UdpClient()
let latestUdpCommand = null
let udpSendTimer = null
let udpSendInFlight = false
let lastUdpSendError = ''
let udpDestinationKey = ''
let udpDestinationGeneration = 0
let acceptedUdpAddresses = new Set()
let udpSendSamples = []
let udpReplyTimestamps = []
let lastUdpReplyAt = 0

udpClient.onBatteryPercentage((batteryPercentage, remoteInfo) => {
    if (
        !latestUdpCommand
        || remoteInfo.port !== latestUdpCommand.port
        || !acceptedUdpAddresses.has(remoteInfo.address)
    ) {
        return
    }

    const receivedAt = Date.now()
    const pendingSample = udpSendSamples.find((sample) => sample.receivedAt === null)
    if (pendingSample) pendingSample.receivedAt = receivedAt
    udpReplyTimestamps.push(receivedAt)
    lastUdpReplyAt = receivedAt

    for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) {
            window.webContents.send('udp:battery-percentage', batteryPercentage)
        }
    }
})

function resetUdpMetrics() {
    udpSendSamples = []
    udpReplyTimestamps = []
    lastUdpReplyAt = 0
}

async function updateAcceptedUdpAddresses(host, generation) {
    try {
        const addresses = net.isIP(host)
            ? [{ address: host }]
            : await dns.lookup(host, { all: true, family: 4 })
        if (generation === udpDestinationGeneration) {
            acceptedUdpAddresses = new Set(addresses.map(({ address }) => address))
        }
    } catch (error) {
        if (generation === udpDestinationGeneration) {
            acceptedUdpAddresses = new Set()
            console.warn('Could not resolve UDP destination host:', error.message)
        }
    }
}

function publishUdpMetrics() {
    const now = Date.now()
    const oldestSample = now - UDP_LOSS_WINDOW_MS
    const settledBefore = now - UDP_REPLY_GRACE_MS
    udpSendSamples = udpSendSamples.filter((sample) => sample.sentAt > oldestSample)
    udpReplyTimestamps = udpReplyTimestamps.filter((timestamp) => timestamp > oldestSample)

    const settledSamples = udpSendSamples.filter((sample) => sample.sentAt <= settledBefore)
    const receivedSamples = settledSamples.filter((sample) => sample.receivedAt !== null)
    const settledReplyCount = udpReplyTimestamps.filter((timestamp) => timestamp <= now).length
    const rxPerSecond = udpReplyTimestamps.filter(
        (timestamp) => timestamp > now - UDP_RX_WINDOW_MS,
    ).length
    const recentRttSamples = receivedSamples.slice(-20)
    const approximateRttMs = recentRttSamples.length
        ? Math.round(recentRttSamples.reduce(
            (total, sample) => total + (sample.receivedAt - sample.sentAt),
            0,
        ) / recentRttSamples.length)
        : null
    const approximateLossPercentage = settledSamples.length
        ? Math.round((Math.max(0, settledSamples.length - settledReplyCount) / settledSamples.length) * 100)
        : null
    const status = !latestUdpCommand
        ? 'disabled'
        : !lastUdpReplyAt
            ? 'waiting'
            : now - lastUdpReplyAt <= UDP_STALE_AFTER_MS
                ? 'connected'
                : 'disconnected'
    const metrics = {
        status,
        rxPerSecond,
        approximateRttMs,
        approximateLossPercentage,
    }

    for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) window.webContents.send('udp:metrics', metrics)
    }
}

async function loadSettings() {
    try {
        const contents = await fs.readFile(settingsPath, 'utf8')
        return JSON.parse(contents)
    } catch (error) {
        if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) {
            console.error('Failed to load settings:', error)
        }
        return {}
    }
}

async function saveSettings(_event, settings) {
    const temporaryPath = `${settingsPath}.tmp`

    await fs.writeFile(temporaryPath, `${JSON.stringify(settings, null, 2)}\n`, 'utf8')
    await fs.rename(temporaryPath, settingsPath)
    return settings
}

async function resolveCameraStream(_event, cameraUrl) {
    if (typeof cameraUrl !== 'string') {
        throw new TypeError('Camera URL must be a string.')
    }

    const sourceUrl = cameraUrl.trim()
    cameraFrameTimestamps = []

    if (!sourceUrl) {
        httpCameraRelay.stop()
        rtspTranscoder.stop()
        return ''
    }

    let protocol
    try {
        protocol = new URL(sourceUrl).protocol
    } catch {
        throw new Error('Camera URL is invalid.')
    }

    if (protocol === 'rtsp:') {
        httpCameraRelay.stop()
        return rtspTranscoder.resolveStreamUrl(sourceUrl)
    }
    if (protocol === 'http:' || protocol === 'https:') {
        rtspTranscoder.stop()
        return httpCameraRelay.resolveStreamUrl(sourceUrl)
    }
    throw new Error('Camera URL must use HTTP, HTTPS, or RTSP.')
}

function publishCameraFps() {
    const cutoff = Date.now() - CAMERA_FPS_WINDOW_MS
    cameraFrameTimestamps = cameraFrameTimestamps.filter((timestamp) => timestamp > cutoff)

    for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) {
            window.webContents.send('camera:fps', cameraFrameTimestamps.length)
        }
    }
}

// Maps the renderer's [yVelocity, thetaVelocity] array onto the named command
// fields declared by the active packet schema, so a schema rename or added
// field does not require renderer changes. Additional fields fall back to their
// schema defaults during encoding.
function buildCommandValues(velocity) {
    const yField = udpClient.codec.fieldNameForRole('command', 'yVelocity')
    const thetaField = udpClient.codec.fieldNameForRole('command', 'thetaVelocity')
    const values = {}
    if (yField) values[yField] = velocity[0]
    if (thetaField) values[thetaField] = velocity[1]
    return values
}

async function sendLatestUdpVelocity() {
    if (!latestUdpCommand || udpSendInFlight) return

    const command = latestUdpCommand
    const sendSample = { sentAt: Date.now(), receivedAt: null }
    udpSendSamples.push(sendSample)
    udpSendInFlight = true
    try {
        await udpClient.sendCommand(command.host, command.port, buildCommandValues(command.velocity))
        lastUdpSendError = ''
    } catch (error) {
        udpSendSamples = udpSendSamples.filter((sample) => sample !== sendSample)
        const message = error instanceof Error ? error.message : String(error)
        if (message !== lastUdpSendError) {
            console.error('Failed to send UDP velocity:', error)
            lastUdpSendError = message
        }
    } finally {
        udpSendInFlight = false
    }
}

function updateUdpVelocity(_event, host, port, velocity) {
    const normalizedHost = host.trim()
    const destinationKey = `${normalizedHost}:${port}`
    if (destinationKey !== udpDestinationKey) {
        udpDestinationKey = destinationKey
        udpDestinationGeneration += 1
        acceptedUdpAddresses = new Set()
        resetUdpMetrics()
        void updateAcceptedUdpAddresses(normalizedHost, udpDestinationGeneration)
    }

    latestUdpCommand = {
        host: normalizedHost,
        port,
        velocity: [...velocity],
    }

    if (!udpSendTimer) {
        udpSendTimer = setInterval(sendLatestUdpVelocity, UDP_SEND_INTERVAL_MS)
    }
}

function stopUdpVelocity() {
    latestUdpCommand = null
    udpDestinationKey = ''
    udpDestinationGeneration += 1
    acceptedUdpAddresses = new Set()
    resetUdpMetrics()
    lastUdpSendError = ''
    if (udpSendTimer) {
        clearInterval(udpSendTimer)
        udpSendTimer = null
    }
}

// ================================= //

function createWindow() {
    const win = new BrowserWindow({
        width: 1280,
        height: 800,
        minWidth: 960,
        minHeight: 640,
        frame: false,
        backgroundColor: '#f4f6f8',
        webPreferences: {
            preload: path.join(__dirname, 'electron-components', 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    })

    win.webContents.on('did-finish-load', () => {
        win.webContents.setZoomFactor(1.5)
    })

    win.webContents.on('did-start-navigation', (_event, _url, isInPlace, isMainFrame) => {
        if (isMainFrame && !isInPlace) {
            stopUdpVelocity()
        }
    })
    win.webContents.on('render-process-gone', stopUdpVelocity)
    win.on('closed', stopUdpVelocity)

    const devServerUrl = process.env.VITE_DEV_SERVER_URL

    if (devServerUrl) {
        win.loadURL(devServerUrl)
    } else {
        win.loadFile(path.join(__dirname, 'dist', 'index.html'))
    }
}

app.whenReady().then(() => {
    createWindow()
    const cameraFpsTimer = setInterval(publishCameraFps, CAMERA_FPS_PUBLISH_INTERVAL_MS)
    cameraFpsTimer.unref()
    const udpMetricsTimer = setInterval(publishUdpMetrics, UDP_METRICS_PUBLISH_INTERVAL_MS)
    udpMetricsTimer.unref()

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow()
        }
    })

    ipcMain.handle('settings:load', loadSettings)
    ipcMain.handle('settings:save', saveSettings)
    ipcMain.handle('camera:resolve-stream', resolveCameraStream)
    ipcMain.on('udp:update-velocity', updateUdpVelocity)
    ipcMain.on('udp:stop-velocity', stopUdpVelocity)
    ipcMain.on('app:quit', () => app.quit())
})

app.on('before-quit', () => {
    stopUdpVelocity()
    httpCameraRelay.stop()
    rtspTranscoder.stop()
    udpClient.close()
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit()
    }
})
