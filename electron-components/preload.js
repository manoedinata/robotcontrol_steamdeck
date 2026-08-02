const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    quitApp: () => ipcRenderer.send('app:quit'),
    loadSettings: () => ipcRenderer.invoke('settings:load'),
    saveSettings: (settings) => ipcRenderer.invoke('settings:save', settings),
    resolveCameraStream: (cameraUrl) => ipcRenderer.invoke('camera:resolve-stream', cameraUrl),
    updateUdpVelocity: (host, port, velocity) => (
        ipcRenderer.send('udp:update-velocity', host, port, velocity)
    ),
    stopUdpVelocity: () => ipcRenderer.send('udp:stop-velocity'),
    onUdpBatteryPercentage: (callback) => {
        const listener = (_event, batteryPercentage) => callback(batteryPercentage)
        ipcRenderer.on('udp:battery-percentage', listener)
        return () => ipcRenderer.removeListener('udp:battery-percentage', listener)
    },
})