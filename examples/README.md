# Examples

Each script does one thing. Settings are constants at the top of the file: edit them there.
The scripts connect to the saved robot (`menlo setup`), or to the robot the `MENLO_*`
environment variables describe, for example `MENLO_UDP_HOST=192.168.22.32`. Read the
docstring at the top of a file before you run it: it says what the robot must be doing.

The SDK checks the robot before `stand()`, `balance()`, `set_velocity()` and `set_joints()`
send anything. When the robot is not ready, the scripts print what stands in the way and what
fixes it, and exit with nothing sent. Nothing here is an emergency stop: use the E-Stop in Asimov Manager, or
cut power at the battery unit.

## Check

- [check.py](check.py): the same check on its own, for standing, walking and a trajectory; sends nothing.

## Connect

- [connect.py](connect.py): the udp, hybrid and livekit connection modes side by side; set `MODE`.

## State

- [read_state.py](read_state.py): robot mode, arming, faults, battery, actuator temperatures, IMU, alerts and state rate.

## Stand, balance, walk, damp

Run them in this order. The robot must be on its feet, hanging from its gantry hook.

- [stand.py](stand.py): DAMP to STAND; returns once armed.
- [balance.py](balance.py): STAND to MOVE at zero velocity; the walking policy balances the robot in place.
- [walk.py](walk.py): walk forward for 3 s in MOVE; the robot then balances in place.
- [damp.py](damp.py): put the robot in DAMP (every actuator stops holding its position); asks first; the robot must be supported.

## Waiting on the robot

- [wait_until.py](wait_until.py): act the moment the elbow passes an angle, while `set_joints()` is still moving it, with the robot supported.

## Streaming from your own loop

- [stream_velocity.py](stream_velocity.py): send velocity at 50 Hz from your own loop with `set_velocity(hold=False)`, then balance.

## Keyboard

- [keyboard.py](keyboard.py): drive with w, a, s, d, q and e in short steps that stop when you let go; t stands, space balances, b damps, x quits.

## Joints

- [move_joints.py](move_joints.py): bend an elbow with `set_joints()`, with the robot supported.

## Media

- [camera_and_audio.py](camera_and_audio.py): save a photo, a short clip as JPEG frames with its sound as a WAV file, and play a tone on the speaker (hybrid or livekit).
- [record_audio.py](record_audio.py): record the microphone to a WAV file (hybrid or livekit).
- [play_audio.py](play_audio.py): play a WAV file on the speaker (hybrid or livekit).

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
