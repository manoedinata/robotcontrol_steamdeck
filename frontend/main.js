const { app, BrowserWindow, ipcMain } = require('electron')
const fs = require('node:fs/promises')
const path = require('node:path')

const settingsDir = process.env.APP_SETTINGS_DIR
    ? path.resolve(process.env.APP_SETTINGS_DIR)
    : __dirname
const settingsPath = path.join(settingsDir, 'settings.json')

async function ensureSettingsDir() {
    if (process.env.APP_SETTINGS_DIR) {
        try {
            await fs.mkdir(settingsDir, { recursive: true })
        } catch (error) {
            console.error('Failed to create settings directory:', error)
        }
    }
}

async function loadSettings() {
    await ensureSettingsDir()
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
    await ensureSettingsDir()
    const temporaryPath = path.join(settingsDir, `.settings.json.tmp-${process.pid}`)

    await fs.writeFile(temporaryPath, `${JSON.stringify(settings, null, 2)}\n`, 'utf8')
    await fs.rename(temporaryPath, settingsPath)
    return settings
}

function createWindow() {
    const win = new BrowserWindow({
        width: 1280,
        height: 800,
        minWidth: 960,
        minHeight: 640,
        fullscreen: false,
        frame: false,
        autoHideMenuBar: true,
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
        if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })

    ipcMain.handle('settings:load', loadSettings)
    ipcMain.handle('settings:save', saveSettings)
    ipcMain.on('app:quit', () => app.quit())
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
})
