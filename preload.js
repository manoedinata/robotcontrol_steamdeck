const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
    quitApp: () => ipcRenderer.send('app:quit'),
    loadSettings: () => ipcRenderer.invoke('settings:load'),
    saveSettings: (settings) => ipcRenderer.invoke('settings:save', settings),
    resolveCameraStream: (cameraUrl) => ipcRenderer.invoke('camera:resolve-stream', cameraUrl),
    sendUdpMessage: (host, port, header, velocity) => (
        ipcRenderer.invoke('udp:send-message', host, port, header, velocity)
    ),
})