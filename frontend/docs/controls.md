# Controls

## Interface Navigation

| Input      | Action                                                                                                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- |
| D-pad      | On Home: pan/tilt the PTZ camera (see Camera Controls). Within Settings: move focus, and move within the built-in keyboard |
| A          | Open Settings, activate a focused control, or press a keyboard key                                                         |
| B          | Switch to the next camera source on Home; close Settings or cancel the built-in keyboard                                   |
| Left stick | Navigate the built-in keyboard only                                                                                        |

B only switches cameras when more than one source is configured and no modal dialog is open, so Settings and the built-in keyboard keep their cancel behavior.

The camera and robot controller remain mounted while Settings is open. Focus moves spatially, remains visible while the drawer scrolls, and returns to the originating control when the keyboard closes. Saving restores focus to the Save button. Held directional input repeats after an initial delay.

On the Home view, both sticks retain robot-control behavior; interface navigation does not consume those axes.

## Robot Controls

| Input             | Robot value             |
| ----------------- | ----------------------- |
| Left stick up     | Positive Y velocity     |
| Left stick down   | Negative Y velocity     |
| Right stick left  | Negative theta velocity |
| Right stick right | Positive theta velocity |

The Gamepad API reads left-stick Y from `axes[1]` and right-stick X from `axes[2]`. Each value is normalized to `-1..+1` and scaled by its configured limit. Hardware input uses a `0.12` dead zone; pointer and touch input do not. Sideways translation is intentionally absent for the differential-drive robot.

When Y velocity is negative, `ControllerPanel.vue` negates theta before sending it, so steering stays relative to the driver's view while reversing. The displayed theta value is the pre-negation input; the backend sends whatever the renderer publishes without further transformation.

## Camera Controls (PTZ)

| Input                | Camera action                                               |
| -------------------- | ----------------------------------------------------------- |
| D-pad up             | Tilt camera up                                              |
| D-pad down           | Tilt camera down                                            |
| D-pad right          | Pan camera right                                            |
| D-pad left           | Pan camera left                                             |
| RB (hold)            | Zoom in                                                     |
| LB (hold)            | Zoom out                                                    |
| Focus buttons (hold) | On-screen buttons on the left edge of the Home view (mirroring the Settings/Exit stack on the right): focus near / far (single command on press, stop on release) |

All PTZ inputs are active only while the PTZ IP matches the camera on screen (see below).

PTZ requires a PTZ IP address in Settings; the camera credentials are hardcoded in the backend (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`, tried as digest first, then basic). PTZ commands are only sent when the PTZ IP is the host of the camera currently on screen — pan/tilt/zoom/focus would otherwise move a camera the operator cannot see. When they do not match, the on-screen focus buttons are hidden and the D-pad/shoulder PTZ inputs are inert. With a matching address set, holding an input sends a `{"type":"ptz","direction":...,"zoom":...,"focus":...}` message over the controls WebSocket: the D-pad populates `direction`, the shoulder buttons populate `zoom`, and the on-screen focus buttons populate `focus` — three independent channels so panning, zooming, and focusing can combine. On the wire `zoom` uses the backend's `zoom-in`/`zoom-out` and `focus` uses `focus-near`/`focus-far`; `useBackendConnection.js` maps the UI's `in`/`out`/`near`/`far` values before sending. The backend re-sends the held rotation/zoom command at 5 Hz and transmits continuous stop commands once the button is released, so the camera keeps moving between updates and always stops cleanly even if the UI closes. The shoulder buttons are digital (pressed/not pressed), since Electron/Chromium does not reliably surface the analog triggers. Focus is different: it is sent once per press on the ISAPI `FocusData` endpoint (separate from the `PTZData` channel) with a single stop command on release, rather than being repeated at the loop rate. An empty PTZ IP disables the feature entirely; the backend never issues camera HTTP requests.