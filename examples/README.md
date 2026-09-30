# Examples

Each file is one short, runnable script. Examples without a connection mode in their name
use the saved robot (`menlo setup`); set `MENLO_ROBOT=NAME` to pick another. Read the
docstring at the top of a file before you run it: it says what the robot must be doing.

## Connect

- [01_connect_udp.py](01_connect_udp.py): connect over udp and print the robot's state.
- [02_connect_hybrid.py](02_connect_hybrid.py): connect in hybrid mode (UDP control, LiveKit camera and audio).
- [03_connect_livekit.py](03_connect_livekit.py): connect in livekit mode through Asimov Manager.

## Move safely

- [04_preflight.py](04_preflight.py): check readiness to stand, walk or run a trajectory, without sending anything.
- [05_stand_and_walk.py](05_stand_and_walk.py): stand from DAMP, wait until armed, walk for 3 s, stop.
- [06_stop_and_shutdown.py](06_stop_and_shutdown.py): end a walk early with `stop()`, and optionally finish in DAMP.

## State

- [07_read_state.py](07_read_state.py): print robot mode, arming, battery and actuator temperatures, and watch mode changes and alerts.

## Joints

- [08_move_joints.py](08_move_joints.py): move the head with `goto()` on a supported robot.

## Media

- [09_camera_and_audio.py](09_camera_and_audio.py): save a photo and a clip with sound, and play a tone on the speaker.

## Recording

- [10_record_and_replay.py](10_record_and_replay.py): record robot state to a JSON-lines file and read it back.

## Without the SDK

- [11_raw_livekit.py](11_raw_livekit.py): read state and send a command through the LiveKit room with `livekit` and `asimov-protocol` only.

## Apps

- [apps/agent_room.py](apps/agent_room.py): camera and walking as tools for an agent in the robot's room.
- [apps/follow_the_ball.py](apps/follow_the_ball.py): steer towards a magenta ball seen by the robot's camera.
