# Examples

Each script does one thing. Settings are constants at the top of the file: edit them there.
The scripts connect to the saved robot (`menlo setup`), or to the robot the `MENLO_*`
environment variables describe, for example `MENLO_UDP_HOST=192.168.22.32`. Read the
docstring at the top of a file before you run it: it says what the robot must be doing.

Every script that moves the robot first runs `require_ready()` from `check.py`: it prints
what stands in the way and exits without sending anything when the robot is not ready.
Nothing here is an emergency stop: use the E-Stop in Asimov Manager, or cut power at the
battery unit.

## Check

- [check.py](check.py): readiness to stand, walk or run a trajectory; sends nothing.

## Connect

- [connect.py](connect.py): the udp, hybrid and livekit connection modes side by side; set `MODE`.

## State

- [read_state.py](read_state.py): robot mode, arming, faults, battery, actuator temperatures, IMU, alerts and state rate.

## Stand, walk, damp

Run them in this order. The robot must be on its feet, hanging from its gantry hook.

- [stand.py](stand.py): DAMP to STAND, then wait until armed.
- [walk.py](walk.py): walk forward for 3 s, then `stop()`; the robot stays in MOVE, balancing.
- [damp.py](damp.py): every actuator limp; asks first; the robot must be supported.

## Keyboard

- [keyboard.py](keyboard.py): drive with w, a, s, d, q and e in short steps that stop when you let go; t stands, b damps, x quits.

## Joints

- [move_joints.py](move_joints.py): bend an elbow with `goto()`, with the robot supported.

## Media

- [camera_and_audio.py](camera_and_audio.py): save a photo, a short clip as JPEG frames with its sound as a WAV file, and play a tone on the speaker (hybrid or livekit).

## Recording

- [record_and_replay.py](record_and_replay.py): record robot state to a JSON-lines file and read it back.

## Without the SDK

The robot's LiveKit room with `livekit` and `asimov-protocol` only. Set `MANAGER_URL` in
`manager_token.py` and `MENLO_CREDENTIAL` in the environment.

- [livekit_raw/manager_token.py](livekit_raw/manager_token.py): a join token from Asimov Manager, refusing redirects; used by the others.
- [livekit_raw/send_commands.py](livekit_raw/send_commands.py): check the state stream, then send STAND.
- [livekit_raw/read_state.py](livekit_raw/read_state.py): print the robot's state.
- [livekit_raw/camera.py](livekit_raw/camera.py): save one camera frame.
- [livekit_raw/audio.py](livekit_raw/audio.py): record the microphone to a WAV file.

## Apps

Nothing moves until you press g. Space pauses, b damps (asks first), x quits.

- [apps/follow_the_ball.py](apps/follow_the_ball.py): steer towards a magenta ball seen by the robot's camera.
- [apps/agent_room.py](apps/agent_room.py): camera and walking as tools for an agent in the robot's room.
