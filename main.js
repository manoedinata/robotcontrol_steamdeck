const { app, BrowserWindow, ipcMain } = require('electron')
const fs = require('node:fs/promises')
const path = require('node:path')

const settingsPath = path.join(__dirname, 'settings.json')
const defaultSettings = Object.freeze({ cameraUrl: '' })

async function loadSettings() {
    try {
        const contents = await fs.readFile(settingsPath, 'utf8')
        const settings = JSON.parse(contents)

        return {
            cameraUrl: typeof settings.cameraUrl === 'string' ? settings.cameraUrl : '',
        }
    } catch (error) {
        if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) {
            console.error('Failed to load settings:', error)
        }
        return { ...defaultSettings }
    }
}

async function saveSettings(_event, settings) {
    const cameraUrl = typeof settings?.cameraUrl === 'string' ? settings.cameraUrl.trim() : ''
    const nextSettings = { cameraUrl }
    const temporaryPath = `${settingsPath}.tmp`

    await fs.writeFile(temporaryPath, `${JSON.stringify(nextSettings, null, 2)}\n`, 'utf8')
    await fs.rename(temporaryPath, settingsPath)
    return nextSettings
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
    ipcMain.on('app:quit', () => app.quit())
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit()
    }
})
