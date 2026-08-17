# Major UI Revamp

Revamp whole UI to make it more like Drone-like controller. The UI shows camera feed fullscreen, with floating buttons/UIs for e.g. connection status, battery level, and other telemetry data. The buttons should be minimalistic and intuitive, allowing users to easily access essential functions without cluttering the screen.

One of floating buttons should be a "Settings" button that opens a side panel with more detailed options. The side panel should slide in from the right and allow users to adjust camera settings, flight parameters, and other advanced features. Since this is not a view, use "shell" to indicate that the side panel is a separate component that can be toggled on and off.

For now, floating buttons consist of the following:

- **Connection Status**: Displays the current connection status with the camera feed (e.g., connected, disconnected, reconnecting).
- **Battery Level**: Shows the current battery level of the drone or camera device, with a visual indicator (e.g., battery icon) and percentage.
- **IP Address**: Displays the IP address of the device, allowing users to quickly identify the device's network information.

Use placeholder for battery level and IP address. Connection status should be a simple colored dot (green for connected, red for disconnected, yellow for reconnecting) with a tooltip that shows the status text on hover. The IP address should be displayed next to the connection status dot, allowing users to quickly identify the device's network information.