const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    quitApp: () => ipcRenderer.send('app:quit'),
    loadSettings: () => ipcRenderer.invoke('settings:load'),
    saveSettings: (settings) => ipcRenderer.invoke('settings:save', settings),
    resolveCameraStream: (cameraUrl) => ipcRenderer.invoke('camera:resolve-stream', cameraUrl),
    onCameraFps: (callback) => {
        const listener = (_event, fps) => callback(fps)
        ipcRenderer.on('camera:fps', listener)
        return () => ipcRenderer.removeListener('camera:fps', listener)
    },
    onCameraStreamInterrupted: (callback) => {
        const listener = () => callback()
        ipcRenderer.on('camera:stream-interrupted', listener)
        return () => ipcRenderer.removeListener('camera:stream-interrupted', listener)
    },
    updateUdpVelocity: (host, port, velocity) => (
        ipcRenderer.send('udp:update-velocity', host, port, velocity)
    ),
    stopUdpVelocity: () => ipcRenderer.send('udp:stop-velocity'),
    onUdpBatteryPercentage: (callback) => {
        const listener = (_event, batteryPercentage) => callback(batteryPercentage)
        ipcRenderer.on('udp:battery-percentage', listener)
        return () => ipcRenderer.removeListener('udp:battery-percentage', listener)
    },
    onUdpMetrics: (callback) => {
        const listener = (_event, metrics) => callback(metrics)
        ipcRenderer.on('udp:metrics', listener)
        return () => ipcRenderer.removeListener('udp:metrics', listener)
    },
})