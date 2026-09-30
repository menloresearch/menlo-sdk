# Driving an Asimov robot with `menlo.asimov`

You are writing a Python script that drives an Asimov biped through `menlo.asimov`. This page
holds what the script needs: connection, readiness checks, verbs, waits, the safety model and
media. Python 3.12+.

## Install and connect

```bash
pip install menlo-sdk            # the robot is `menlo.asimov`; every connection mode included
menlo setup                      # once per machine: name, connection mode, checks, save
menlo status                     # READY / NOT READY / FAULTED; sends nothing
```

`menlo stand`, `walk` and `damp` print a plan and ask `Proceed? [y/N]`; with no terminal, pass
`--yes` (without it: exit 2, nothing sent). `Not feasible:` and exit 3 mean the robot cannot do
it now; exit 4 means the answer was no.

`menlo setup` asks for the connection mode and only the fields it needs: the robot's address
(udp, hybrid), the Asimov Manager URL (hybrid, livekit) and an SDK credential (hybrid,
livekit). Non-interactive: `menlo robots add lab --mode udp --udp 192.168.22.32`. The SDK
credential comes from the Developer page of Asimov Manager, with the Control role; an
`observe` credential can watch the room but not drive over livekit (hybrid drives over UDP, which has no sign-in).

```python
from menlo.asimov import Mode, Robot

with Robot().connect() as robot:  # the saved robot; returns once the robot has reported state
    ...
```

`Robot()` with no argument resolves, in order: `MENLO_UDP_HOST` and/or `MENLO_MANAGER_URL` +
`MENLO_CREDENTIAL` in the environment, then a saved robot in `~/.menlo/robots.toml`
(`$MENLO_HOME`; the one named by `MENLO_ROBOT`, else the default, else the only one), else
`ConnectError` naming each way. `connect()` with no mode uses the saved robot's connection
mode (or `MENLO_MODE`); `connect("udp")` overrides it. Explicit form:
`Robot(ConnectionConfig(udp=UdpConfig(host), livekit=ManagerConfig(url, credential))).connect("hybrid")`.
Leaving the `with` block calls `close()`.

Connection modes: `udp` (control and state over UDP on the robot's network, no camera),
`hybrid` (control and state over UDP, camera and audio over LiveKit), `livekit`
(everything through the robot's LiveKit room, wherever Asimov Manager is reachable).

`connect(require_state=False)` opens the connection without waiting for the firmware: camera,
microphone and speaker work at once; `robot.state`, `robot.info` and every motion verb raise
`NotConnectedError` until the robot reports, then unlock on their own.

## Check before you move

```python
robot.preflight("move")  # Preflight: .ok, .problems, .has(code); sends nothing
robot.wait_ready("move")  # blocks until ok (timeout 5 s); NotReadyError otherwise
robot.armed  # True / False / None
```

Actions: `"stand"`, `"move"` (`set_velocity`), `"trajectory"` (`trajectory`, `goto`). Problem
codes: `not_connected`, `no_state`, `stale_state` (older than 0.5 s), `faulted`,
`battery_protecting`, `battery_low` (below 20 %), `joint_hot` (60 C or more), `wrong_mode`,
`not_armed`; warnings (not blocking) `unknown_battery`, `unknown_joint_temp`,
`unknown_gravity` (with it, `move` passes in STAND without arming being checked). `wait_ready`
raises at once on `faulted` or `not_connected`.

**Armed:** the firmware reports STAND at once but accepts MOVE only after STAND has been held
upright for 0.5 s. A velocity sent earlier is neither refused nor reported: the robot stays
in STAND while the `duration` runs. Always `wait_ready("move")` between `stand()` and the
first `set_velocity`.

## The verbs

Each verb returns a `Sent` at once; nothing blocks unless you ask.

| verb | what it does |
|---|---|
| `set_velocity(vx=0, vy=0, vyaw=0, *, duration=None, wait=False) -> Sent` | walk: `vx` forward m/s, `vy` left m/s, `vyaw` counter-clockwise rad/s. Held (re-sent at 10 Hz) until `stop()`, another verb, or `duration` s, then zero is sent. Clamped to `Limits()`, the firmware caps 0.4 m/s, 0.4 m/s, 0.8 rad/s; `Sent.clamped` says so. |
| `stop() -> Sent` | zero velocity. The robot stays in MOVE, balancing in place: this is how a walk ends. |
| `stand() -> Sent` | DAMP to STAND. Holds a pose without a balance loop. Only from DAMP; never after walking. |
| `damp() -> Sent` | every actuator compliant now; a standing robot folds. Not an emergency stop: use the E-Stop in Asimov Manager. |
| `trajectory(positions, *, kp=None, kd=None) -> Sent` | one joint setpoint (radians, `robot.info.dof` values, firmware order). Walking policy off. `goto(positions, duration=2.0, wait=True)` interpolates from the current pose and holds. Robot must be supported. Joint control ends with `damp()`, with the robot still supported. |

`set_velocity` returns at once. Pass `wait=True` to block until the hold ended *and* its
zero was sent, or `time.sleep(duration)`. A verb called mid-hold (including `close()` at the
end of a `with`) cuts the walk short.

## Waits

A wait reads the robot's own state report, not a timer.

```python
robot.wait_for(Mode.STAND, timeout=15.0) -> State          # robot mode reached
robot.wait_until(lambda s: s.upright and s.mode is Mode.MOVE, timeout=10.0) -> State
```

Both raise typed errors when the answer cannot come: `WaitTimeoutError` (a `TimeoutError`,
`.last` = last state), `StateStaleError` (stream went quiet), `RobotFaultedError` (the
firmware latched DAMP; checked before your predicate, so a fall is never read as success).

`robot.state` is the latest sample: `.mode` (`Mode.DAMP|STAND|MOVE`), `.upright`, `.faulted`,
`.yaw` (rad, counter-clockwise, wraps in (-π, π]; `None` without IMU), `.euler`, `.quat`,
`.gyro`, `.gravity`, `.joints`/`.joint("L_Knee").pos`, `.battery` (may be `None`), `.age_s`.
`robot.info`: `dof`, `joint_names`, `capabilities`, `endpoint`. `robot.has("camera")`.

## Safety model

- Nothing in the SDK is an emergency stop. Use the E-Stop in Asimov Manager, or cut power at the battery unit.
- The SDK re-sends a held velocity at 10 Hz and sends zero on `stop()`, and on `close()`,
  the end of a `with` block or a lost link while it holds a velocity.
- On udp and hybrid, Asimov Edge zeroes velocity 2 s after the last one it received, so a
  crashed script leaves the robot standing in MOVE, not walking. On livekit, Asimov Edge stops
  a held velocity when the SDK sends zero or leaves the room.
- A trajectory not re-sent for 2 s makes Asimov Edge switch the robot to DAMP (the robot folds).
- `LinkLostError` (no state for 2 s): the SDK sends zero and every verb raises until you
  `close()` + `connect()` again.
- `stand()` holds a pose without a balance loop. Wake-up only (DAMP, then STAND, then MOVE): call it only
  from DAMP, after `wait_ready("stand")`. A free-standing robot asked to STAND after walking tips
  over and latches a fault-DAMP until the firmware restarts. End a walk with `stop()`.
- In DAMP, Asimov Edge drops velocities: `stand()`, `wait_for(Mode.STAND)` and
  `wait_ready("move")` first.
- **Faults latch.** A fall, actuator over-temperature, battery protection, a joint past its
  limit, lost actuator communication or a watchdog makes the firmware latch DAMP until it
  restarts; `state.faulted` stays True. Nothing a script sends clears it.
- **Control priority.** Asimov Edge obeys the Asimov Manager Cockpit, then a paired gamepad,
  then udp, then livekit. A paired gamepad holds control even when idle, and the state stream
  does not show who holds control: if the robot ignores you, check for another controller.

## Media

```python
frame = robot.camera.photo(timeout=5.0)      # one fresh Frame (rgb8)
frame.to_jpeg(quality=85) -> bytes           # for a vision model / upload; needs Pillow
frame.to_numpy()                             # (h, w, 3) uint8; needs numpy
robot.camera.latest()                        # newest frame or None, never blocks; check .age_s
for frame in robot.camera.frames(timeout=5.0): ...   # latest-wins iterator
for chunk in robot.microphone.chunks(timeout=5.0): chunk.data  # pcm_s16le, in order
robot.speaker.play_pcm(pcm_s16le_bytes, sample_rate_hz=16_000, channels=1)
clip = robot.camera.capture_clip(5.0, audio=True); clip.save_wav("a.wav"); clip.save_mp4("v.mp4")
```

Media needs hybrid or livekit. A capability the connection does not carry raises
`UnsupportedError`; gate with `robot.has("camera")`.

## Example 1: walk forward for 3 s, end standing still

```python
from menlo.asimov import Mode, Robot

with Robot().connect() as robot:
    if robot.state.mode is Mode.DAMP:  # stand() only from DAMP
        robot.wait_ready("stand")
        robot.stand()
        robot.wait_for(Mode.STAND)
    robot.wait_ready("move")  # armed: STAND held upright for 0.5 s
    robot.set_velocity(vx=0.2, duration=3.0, wait=True)  # 0.2 m/s for 3 s, zero sent, returns
    robot.stop()  # MOVE at zero velocity: the robot balances in place
    # Do not call robot.stand() here: it would stiffen a balancing robot and tip it over.
```

The same script is [examples/05_stand_and_walk.py](../examples/05_stand_and_walk.py).

## Example 2: turn 180° and verify from the IMU

```python
import math
from menlo.asimov import Mode, Robot

RATE = 0.6  # rad/s, under the firmware's 0.8 rad/s cap
SECONDS = math.pi / RATE  # 5.2 s for a half turn, open loop

with Robot().connect() as robot:
    if robot.state.mode is Mode.DAMP:
        robot.wait_ready("stand")
        robot.stand()
        robot.wait_for(Mode.STAND)
    robot.wait_ready("move")
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
    robot.stop()
```

`yaw` is IMU heading relative to wherever the firmware booted, so only differences mean
anything, and both differences are wrapped with `math.remainder(..., math.tau)`: a 190° turn
reads as `turned = -170°`, and the wrapped residual is then -10° (turn back), not +350°.

More: [examples/README.md](../examples/README.md).

## Gotchas

- **`set_velocity` does not block.** The next verb, or the end of the `with` block, cuts
  the hold short. `wait=True`, or sleep for the duration.
- **`stand()` then `set_velocity()` at once does nothing for 0.5 s.** The robot arms only
  after 0.5 s upright in STAND. `wait_ready("move")` in between.
- **`connect()` waits for state.** Firmware off: `ConnectError: no state from the robot ...`
  after `timeout` (default 5 s). Media-only work: `connect(require_state=False)`.
- **No state on udp or hybrid.** Asimov Edge needs `udp-control` on and
  `udp-state-host` set to this machine; it sends UDP state to one address.
- **Two scripts, one credential.** Each session gets its own identity
  (`sdk-<credential id>-<host>-<random>`), so two connects coexist. Pass
  `ManagerConfig(label=...)` only if you want a fixed name: two sessions with the same label
  evict each other (LiveKit keys participants by identity).
- **Trajectories need support.** `trajectory()`/`goto()` switch off the walking policy.
  Joint control ends with `damp()`, with the robot still supported.
- **Errors:** robot/link errors subclass `MenloError` (`ConnectError`, `NotConnectedError`,
  `NotReadyError`, `LinkLostError`, `WaitTimeoutError`, `StateStaleError`,
  `RobotFaultedError`, `UnsupportedError`); your own mistakes are builtins (`ValueError` for a
  bad speed or duration, `KeyError` for a joint name).
- **Outcomes:** `sent.wait_outcome()` is `Unknown` on every connection mode, because Asimov
  Edge reports no per-command verdict. Read success from `robot.state`, never infer it from
  what was sent.
