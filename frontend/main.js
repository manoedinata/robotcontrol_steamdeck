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

// The Deck's own battery, straight from sysfs. The container sees the host's
// /sys, so this reads the same files in development and under Docker. Anything
// missing means "not a device with a battery" rather than an error: the HUD
// shows the source as unavailable and keeps working.
const POWER_SUPPLY_DIR = '/sys/class/power_supply'

async function readDeckBattery() {
    let entries
    try {
        entries = await fs.readdir(POWER_SUPPLY_DIR)
    } catch {
        return null
    }

    // BAT0/BAT1 sort ahead of the AC adapter, and the Deck's pack is BAT1.
    for (const entry of entries.sort()) {
        const supply = path.join(POWER_SUPPLY_DIR, entry)
        try {
            const type = await fs.readFile(path.join(supply, 'type'), 'utf8')
            if (type.trim() !== 'Battery') continue

            const capacity = Number.parseInt(
                await fs.readFile(path.join(supply, 'capacity'), 'utf8'),
                10,
            )
            if (!Number.isInteger(capacity)) continue

            // Absent on some supplies; the level is the part worth having.
            const status = await fs
                .readFile(path.join(supply, 'status'), 'utf8')
                .catch(() => '')

            return {
                level: Math.min(100, Math.max(0, capacity)),
                charging: status.trim() === 'Charging',
            }
        } catch {
            // A supply that cannot be read is not the one we are looking for.
        }
    }

    return null
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
        fullscreen: true,
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

    ipcMain.handle('deck-battery:read', readDeckBattery)
    ipcMain.handle('settings:load', loadSettings)
    ipcMain.handle('settings:save', saveSettings)
    ipcMain.on('app:quit', () => app.quit())
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
})
