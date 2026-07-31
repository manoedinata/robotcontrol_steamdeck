const { app, BrowserWindow, ipcMain } = require('electron')
const fs = require('node:fs/promises')
const path = require('node:path')
const { RtspTranscoder } = require('./rtsp-transcoder')

const settingsPath = path.join(__dirname, 'settings.json')
const rtspTranscoder = new RtspTranscoder()

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

function createWindow() {
    const win = new BrowserWindow({
        width: 1280,
        height: 800,
        minWidth: 960,
        minHeight: 640,
        frame: false,
        backgroundColor: '#f4f6f8',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    })

    win.webContents.on('did-finish-load', () => {
        win.webContents.setZoomFactor(1.2)
    })

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
    ipcMain.on('app:quit', () => app.quit())
})

app.on('before-quit', () => rtspTranscoder.stop())

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit()
    }
})
