const { app, BrowserWindow, ipcMain } = require('electron')
const fs = require('node:fs/promises')
const path = require('node:path')
const { RtspTranscoder } = require('./electron-components/rtsp-transcoder')
const { UdpClient } = require('./electron-components/udp-client')

const settingsPath = path.join(__dirname, 'settings.json')
const UDP_SEND_INTERVAL_MS = 20
const rtspTranscoder = new RtspTranscoder()
const udpClient = new UdpClient()
let latestUdpCommand = null
let udpSendTimer = null
let udpSendInFlight = false
let lastUdpSendError = ''

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

    return rtspTranscoder.resolveStreamUrl(cameraUrl)
}

async function sendLatestUdpVelocity() {
    if (!latestUdpCommand || udpSendInFlight) return

    const command = latestUdpCommand
    udpSendInFlight = true
    try {
        await udpClient.sendMessage(command.host, command.port, 'ITS', command.velocity)
        lastUdpSendError = ''
    } catch (error) {
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
    latestUdpCommand = {
        host: host.trim(),
        port,
        velocity: [...velocity],
    }

    if (!udpSendTimer) {
        udpSendTimer = setInterval(sendLatestUdpVelocity, UDP_SEND_INTERVAL_MS)
    }
}

function stopUdpVelocity() {
    latestUdpCommand = null
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
        win.webContents.setZoomFactor(1.25)
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
    rtspTranscoder.stop()
    udpClient.close()
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit()
    }
})
