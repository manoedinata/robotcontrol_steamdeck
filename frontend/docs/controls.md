# Controls

## Interface Navigation

| Input      | Action                                                                     |
| ---------- | -------------------------------------------------------------------------- |
| D-pad      | Select Settings or Exit, or move within Settings and the built-in keyboard |
| A          | Open Settings, activate a focused control, or press a keyboard key         |
| B          | Close Settings, or cancel the built-in keyboard                            |
| Left stick | Navigate the built-in keyboard only                                        |

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