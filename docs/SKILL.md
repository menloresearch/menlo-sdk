# Driving an Asimov robot with `asimov_sdk`

You are writing a Python script that drives an Asimov biped through `asimov_sdk`. This page
is everything the script needs to be right the first time. Python 3.12+.

## Install and connect

```bash
pip install "asimov-sdk[livekit] @ git+https://github.com/menloresearch/asimov-sdk.git"
asimov login http://<robot-ip> --credential <credential>   # once per machine; validates, saves
```

The credential is minted **on the robot** (`asimovctl sdk-token create --name laptop --role
control`, or the manager's `/sdk` page); `observe` credentials can watch but not drive. The
manager URL is the robot's web UI address — `http://192.168.22.32` (port 80) or
`http://asimov.local:8080`, whatever the robot serves; no port is assumed.

```python
from asimov_sdk import Robot, Mode

with Robot().connect() as robot:  # finds the robot; returns once the robot has reported state
    ...
```

`Robot()` with no argument resolves, in order: `ASIMOV_MANAGER_URL` + `ASIMOV_CREDENTIAL` in
the environment → `~/.asimov/robots.toml` (`$ASIMOV_HOME`; the robot named by `ASIMOV_ROBOT`,
else the default, else the only one) → `ConnectError` naming both. Explicit form:
`Robot(ConnectionConfig(livekit=ManagerConfig(url, credential)))`. `connect()` with no mode
uses the config's one lane (`"livekit"` for a manager); pass `"udp"`/`"hybrid"` only for a
LAN config with a `UdpConfig`. `connect(persist=True)` (or `ASIMOV_PERSIST=1`) saves a working
URL + credential to the store after success. Leaving the `with` block calls `close()`.

`connect(require_state=False)` opens the media lane without waiting for the firmware: camera,
microphone and speaker work at once; `robot.state`, `robot.info` and every motion verb raise
`NotConnectedError` until the robot reports, then unlock on their own. Use it for a
camera-only script or when the firmware may be off.

## The five verbs (each returns a `Sent` immediately — nothing blocks unless you ask)

| verb | what it does |
|---|---|
| `set_velocity(vx=0, vy=0, vyaw=0, *, duration=None, wait=False) -> Sent` | walk: `vx` forward m/s, `vy` left m/s, `vyaw` counter-clockwise rad/s. Held (re-sent at 10 Hz) until `stop()`, another verb, or `duration` s, then zero is sent. Clamped to `Limits(vx=0.6, vy=0.6, vyaw=1.5)`; `Sent.clamped` says so. |
| `stop() -> Sent` | zero velocity. The robot stays in MOVE, balancing in place — **this is how it stands still**. |
| `stand() -> Sent` | the wake-up verb: DAMP → STAND. A **stiffen** with no balance loop. Never after walking (see safety). |
| `damp() -> Sent` | motors go limp NOW. The emergency stop; a standing robot folds. |
| `trajectory(positions, *, kp=None, kd=None) -> Sent` | one joint setpoint (radians, `robot.info.dof` values, firmware order). Walking policy off. `goto(positions, duration=2.0, wait=True)` interpolates from the current pose and holds. Robot must be supported. |

`set_velocity` **returns at once**. Either `time.sleep(duration)` before the next command or
`close()`, or pass `wait=True` to block until the hold ended *and* its zero was sent. A
verb called mid-hold (including `close()` at the end of a `with`) cuts the walk short.

## Waits — read the robot's own report, never a guessed sleep

```python
robot.wait_for(Mode.STAND, timeout=15.0) -> State          # mode reached
robot.wait_until(lambda s: s.upright and s.mode is Mode.MOVE, timeout=10.0) -> State
```

Both raise typed errors when the answer cannot come: `WaitTimeoutError` (a `TimeoutError`,
`.last` = last state), `StateStaleError` (stream went quiet), `RobotFaultedError` (the
firmware fault-DAMPed — checked before your predicate, so a fall is never read as success).

`robot.state` is the latest sample: `.mode` (`Mode.DAMP|STAND|MOVE`), `.upright`, `.faulted`,
`.yaw` (rad, counter-clockwise, wraps in (-π, π]; `None` without IMU), `.euler`, `.quat`,
`.gyro`, `.gravity`, `.joints`/`.joint("L_Knee").pos`, `.battery` (may be `None`), `.age_s`.
`robot.info`: `dof`, `joint_names`, `capabilities`, `endpoint`. `robot.has("camera")`.

## Safety model — what happens when your script stops talking

- The SDK re-sends a held velocity at 10 Hz. The **edge zeroes velocity 2 s after the last
  one it received**, so a crashed script leaves the robot standing in MOVE, not walking.
- A trajectory not re-sent for 2 s is **DAMPed** by the edge (the robot folds).
- `close()` sends a zero if a velocity is held, then drops the link. `LinkLostError` (no state
  for 2 s) does the same and every verb raises until you `close()` + `connect()` again.
- Speeds are clamped at 0.6 m/s / 0.6 m/s / 1.5 rad/s before they leave.
- `stand()` = stiffen, no balance. Wake-up only (DAMP→STAND→MOVE), on a robot that is held,
  on its stand, or already standing still. A free-standing robot asked to STAND after walking
  tips over and latches a fault-DAMP until the firmware restarts. **End a walk with `stop()`.**
- From DAMP the edge drops velocities: `stand()` and `wait_for(Mode.STAND)` first.
- `damp()` is deliberate and never implied. In an emergency, kill the process: the edge's
  watchdog does the rest.

## Media

```python
frame = robot.camera.photo(timeout=5.0)      # ONE fresh Frame (rgb8, 1280x720 on Asimov 1)
frame.to_jpeg(quality=85) -> bytes           # for a vision model / upload; needs Pillow
frame.to_numpy()                             # (h, w, 3) uint8; needs numpy
robot.camera.latest()                        # newest frame or None, never blocks
for frame in robot.camera.frames(timeout=5.0): ...   # latest-wins iterator
for chunk in robot.microphone.chunks(timeout=5.0): chunk.data  # pcm_s16le, in order
robot.speaker.play_pcm(pcm_s16le_bytes, sample_rate_hz=16_000, channels=1)
clip = robot.camera.capture_clip(5.0, audio=True); clip.save_wav("a.wav"); clip.save_mp4("v.mp4")
```

A capability the room does not carry raises `UnsupportedError`; gate with `robot.has("camera")`.

## Example 1 — walk forward for 1 s, end standing still

```python
import time
from asimov_sdk import Mode, Robot

with Robot().connect() as robot:
    if robot.state.mode is Mode.DAMP:  # wake up: the only place stand() belongs
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=15.0)
    robot.set_velocity(vx=0.25, duration=1.0, wait=True)  # 0.25 m/s for 1 s, zero sent, returns
    robot.wait_for(Mode.MOVE, timeout=5.0)
    print("standing still (MOVE at zero velocity):", robot.state.mode.name, robot.state.upright)
    # Do NOT call robot.stand() here — it would stiffen a balancing robot and tip it over.
    # Leaving the block closes the link; the robot keeps balancing in place.
```

Without `wait=True`: `robot.set_velocity(vx=0.25, duration=1.0); time.sleep(1.2)`.

## Example 2 — spin 180° and verify from the IMU

```python
import math
from asimov_sdk import Mode, Robot

RATE = 0.9  # rad/s, under the 1.5 clamp
SECONDS = math.pi / RATE  # 3.49 s for a half turn, open loop

with Robot().connect() as robot:
    if robot.state.mode is Mode.DAMP:
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=15.0)
    before = robot.state.yaw
    robot.set_velocity(vyaw=RATE, duration=SECONDS, wait=True)  # + is counter-clockwise
    after = robot.wait_until(lambda s: s.age_s < 0.2, timeout=2.0).yaw
    turned = math.remainder(after - before, math.tau)  # wrapped difference, in (-π, π]
    print(f"turned {math.degrees(turned):+.0f}° (asked +180°)")
    residual = math.remainder(math.pi - turned, math.tau)  # what is left of the half turn
    if abs(residual) > math.radians(15):  # the gait slips; correct once
        robot.set_velocity(
            vyaw=math.copysign(RATE, residual), duration=abs(residual) / RATE, wait=True
        )
```

`yaw` is IMU heading relative to wherever the firmware booted, so only differences mean
anything, and both differences are wrapped with `math.remainder(..., math.tau)`: a 190° turn
reads as `turned = -170°`, and the wrapped residual is then -10° (turn back), not +350°.

## Gotchas

- **`set_velocity` does not block.** The next verb — or the end of the `with` block — cuts
  the hold short. `wait=True`, or sleep for the duration.
- **`connect()` waits for state.** Firmware off → `ConnectError: no state from the robot …`
  after `timeout` (default 5 s). Media-only work: `connect(require_state=False)`.
- **Two scripts, one credential.** Each session gets its own identity
  (`sdk-<credential id>-<host>-<random>`), so two connects coexist. Pass
  `ManagerConfig(label=...)` only if you want a fixed name — two sessions with the same label
  evict each other (LiveKit keys participants by identity).
- **The manager says `ws://localhost:7880`.** That is the robot's view of its own SFU; the
  SDK substitutes the manager's host. Nothing to do; if you build a `LiveKitConfig` by
  hand, use the robot's IP.
- **`stand()` after a walk tips the robot over.** `stop()` is how a walking robot stands
  still. STAND is for waking up from DAMP.
- **Trajectories need support.** `trajectory()`/`goto()` switch off the walking policy.
- **Errors:** robot/link errors subclass `AsimovError` (`ConnectError`, `NotConnectedError`,
  `LinkLostError`, `WaitTimeoutError`, `StateStaleError`, `RobotFaultedError`,
  `UnsupportedError`); your own mistakes are builtins (`ValueError` for a bad speed or
  duration, `KeyError` for a joint name).
- **Outcomes:** `sent.wait_outcome()` is `Unknown` on today's wire; success is read from
  `robot.state`, never inferred from what was sent.
