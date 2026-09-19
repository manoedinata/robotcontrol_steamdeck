# Controls

## Interface Navigation

| Input      | Action                                                                                                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- |
| D-pad      | On Home: pan/tilt the PTZ camera only (see Camera Controls); it moves no focus there. Within Settings or Recordings: move focus, and move within the built-in keyboard. On a slider, left/right set it instead of moving focus |
| A          | Activate the focused control (on Home only the Settings/Exit/Recordings button that already has focus) or press a keyboard key |
| B          | Switch to the next camera source on Home; close Settings, Recordings, or the recording player; cancel the built-in keyboard |
| Left stick | Navigate the built-in keyboard only                                                                                        |

B only switches cameras when more than one source is configured and no modal dialog is open, so Settings and the built-in keyboard keep their cancel behavior.

The camera and robot controller remain mounted while Settings is open. Focus moves spatially, remains visible while the drawer scrolls, and returns to the originating control when the keyboard closes. Saving restores focus to the Save button. Held directional input repeats after an initial delay.

On the Home view, both sticks retain robot-control behavior and the D-pad stays with the camera; interface navigation consumes neither. The Settings, Recordings, and Exit buttons are tapped on the touchscreen, and focus returns to the button that opened a page when it closes, so A reopens it. The battery readout in the top right is tapped too: it shows the robot's battery or the Deck's own, one at a time, and each tap swaps which. It carries no gamepad binding, since the D-pad on Home belongs to the camera.

The recordings page navigates the same way Settings does, at a higher handler priority; the two are never open at once. A on a session expands it, and A on a file plays it. While the player is up it takes the D-pad entirely -- focus cannot walk back onto the list behind it -- and B closes the player before it closes the page. The player's start-from slider is the one control where left/right set a value rather than moving focus, in twentieths of the clip.

## Robot Controls

| Input                       | Robot value                                     |
| --------------------------- | ----------------------------------------------- |
| Left stick up, forward mode | Positive Y velocity                             |
| Left stick up, reverse mode | Negative Y velocity                             |
| Left stick down             | Nothing; the drive axis has no lower half       |
| Right stick left            | Negative theta velocity                         |
| Right stick right           | Positive theta velocity                         |

The Gamepad API reads left-stick Y from `axes[1]` and right-stick X from `axes[2]`. Each value is normalized to `-1..+1` and scaled by its configured limit. Hardware input uses a `0.12` dead zone; pointer and touch input do not. Sideways translation is intentionally absent for the differential-drive robot.

The left stick travels up only. Which way the robot goes is a mode, not a side of centre: the drive-direction button in the left-hand shell stack (below the PTZ focus and light buttons) switches between **forward**, where the stick's travel is sent as positive Y velocity, and **reverse**, where the same travel is sent negated. The button shows an up arrow in forward and a filled-in down arrow in reverse, and the linear readout goes negative in reverse, so the mode is legible from two places. The readout does not name the mode in words: its width is what holds the stick still, and a label that changed length would move the stick it sits under. It is deliberately not persisted: every start is forward.

Pushing the stick down does nothing in either mode. The pointer puck cannot be dragged past centre and the lower half of the ring is dimmed to say so; the hardware stick's lower half is clamped away, so holding it down is exactly as if it were centred. Reverse motion needs a `yVelocity` minimum below zero -- a limit narrowed to `0` in Settings leaves reverse with nothing to send.

When Y velocity is negative, `ControllerPanel.vue` negates theta before sending it, so steering stays relative to the driver's view while reversing. In reverse mode that applies to every stick push, since they all publish a negative Y. The displayed theta value is the pre-negation input; the backend sends whatever the renderer publishes without further transformation.

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
| Infrared light (tap) | On-screen button below the focus pair: switches the camera's IR illuminator on and off. Latched -- it stays where it is put, and the button is lit while the light is |

All PTZ inputs are active only while the PTZ IP matches the camera on screen (see below).

PTZ requires a PTZ IP address in Settings; the camera credentials are hardcoded in the backend (`PTZ_USERNAME`/`PTZ_PASSWORD` in `PTZController.py`, tried as digest first, then basic). PTZ commands are only sent when the PTZ IP is the host of the camera currently on screen — pan/tilt/zoom/focus would otherwise move a camera the operator cannot see. When they do not match, the on-screen focus buttons are hidden and the D-pad/shoulder PTZ inputs are inert. Opening Settings has the same effect: the drawer navigates with the D-pad, so `usePTZState` holds back every PTZ request and pushes a stop while it is open, and the camera resumes taking input once it closes. With a matching address set, holding an input sends a `{"type":"ptz","direction":...,"zoom":...,"focus":...}` message over the controls WebSocket: the D-pad populates `direction`, the shoulder buttons populate `zoom`, and the on-screen focus buttons populate `focus` — three independent channels so panning, zooming, and focusing can combine. On the wire `zoom` uses the backend's `zoom-in`/`zoom-out` and `focus` uses `focus-near`/`focus-far`; `useBackendConnection.js` maps the UI's `in`/`out`/`near`/`far` values before sending. The backend re-sends the held rotation/zoom command at 5 Hz and transmits continuous stop commands once the button is released, so the camera keeps moving between updates and always stops cleanly even if the UI closes. The shoulder buttons are digital (pressed/not pressed), since Electron/Chromium does not reliably surface the analog triggers. Focus is different: it is sent once per press on the ISAPI `FocusData` endpoint (separate from the `PTZData` channel) with a single stop command on release, rather than being repeated at the loop rate. An empty PTZ IP disables the feature entirely; the backend never issues camera HTTP requests.

The infrared light is further out still: nothing about it is held, so it travels as its own `{"type":"ptz_light","on":true}` message and reaches the camera's `PTZAux` aux control. The renderer does not track it -- the camera keeps the light burning across a UI reload, so the backend states the truth on connect and broadcasts every change, and the button reflects that rather than its own last press. Switching to a camera the PTZ IP does not match hides the button without switching the light off: it is a setting the operator left on, not an input they were holding.