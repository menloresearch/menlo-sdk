---
name: menlo-sdk
description: How to write and run Python scripts that drive a Menlo Asimov robot with menlo-sdk (`import menlo.asimov`). Use when code connects to an Asimov over udp, hybrid or livekit, guards on the robot's state, stands, balances, walks with set_velocity, moves joints, reads robot state, uses the camera, microphone or speaker, or when a script must follow the robot's safety rules.
license: Apache-2.0
---

# Driving an Asimov robot with `menlo.asimov`

You are writing a Python script that drives an Asimov biped through `menlo.asimov`. This page
holds what the script needs: connection, guards, verbs, waits, the safety model and media. Python 3.12+.

## Documentation

This page is the short form. The full documentation is at https://docs.menlo.ai/sdk, and two
files there are meant for agents:

- https://docs.menlo.ai/llms.txt: every docs page, one line each; the SDK pages are under
  "Python SDK".
- https://docs.menlo.ai/llms-full.txt: the full text of every docs page in one file.

Read https://docs.menlo.ai/sdk/safety before a script moves the robot. Every class, argument
and error is in https://docs.menlo.ai/sdk/reference, and what each error means and what to do
about it is in https://docs.menlo.ai/sdk/troubleshooting.

## Install and connect

```bash
pip install menlo-sdk            # the robot is `menlo.asimov`; every connection mode included
menlo setup                      # once per machine: name, connection mode, checks, save
menlo status                     # the robot's facts: READY / NOT READY / FAULTED; sends nothing
```

`menlo stand`, `balance`, `walk` and `damp` print the plan with the robot's facts (robot
mode, armed, faults, active alerts, hottest joint, battery) and ask `Proceed? [y/N]`
(`balance` in MOVE sends zero velocity at once, without asking); with no terminal, pass
`--yes` (without it: exit 2, nothing sent). The facts are for you to judge: nothing is
refused because of them. `menlo stand` from MOVE warns first: support the robot. Exit 3 means
no live state (`Not feasible:`, nothing sent) or that the robot did not get there (not armed
in time, still in DAMP, a fault); exit 4 means the answer was no.

`menlo setup` asks for the connection mode and only the fields it needs: the robot's address
(udp, hybrid), the Asimov Manager URL (hybrid, livekit) and an SDK credential (hybrid,
livekit). Non-interactive: `menlo robots add lab --mode udp --udp 192.168.22.32`. The SDK
credential comes from the Developer page of Asimov Manager, with the Control role; an
`observe` credential can watch the room but not drive over livekit (hybrid drives over UDP, which has no sign-in).

```python
from menlo.asimov import Robot

with Robot().connect() as robot:  # the saved robot; returns once the robot has reported state
    ...
```

`Robot()` with no argument resolves, in order: `MENLO_UDP_HOST` and/or `MENLO_MANAGER_URL` +
`MENLO_CREDENTIAL` in the environment, then a saved robot in `~/.menlo/robots.toml`
(`$MENLO_HOME`; the one named by `MENLO_ROBOT`, else the default, else the only one), else
`ConnectError` naming each way. `connect()` with no mode uses the saved robot's connection
mode (or `MENLO_MODE`), else the mode the config implies (udp only: `udp`; livekit only:
`livekit`; both: `hybrid`); `connect("udp")` overrides it. Explicit form:
`Robot(ConnectionConfig(udp=UdpConfig(host), livekit=ManagerConfig(url, credential))).connect()`
(hybrid).
Leaving the `with` block calls `close()`.

Connection modes: `udp` (control and state over UDP on the robot's network, no camera),
`hybrid` (control and state over UDP, camera and audio over LiveKit), `livekit`
(everything through the robot's LiveKit room, wherever Asimov Manager is reachable).

`connect(require_state=False)` opens the connection without waiting for the firmware: camera,
microphone and speaker work at once; `robot.get_state()`, `robot.info` and `damp()` raise
`NotConnectedError` until the robot reports, then unlock on their own. `stand()`, `balance()`,
`set_velocity()`, `trajectory()` and `set_joints()` wait up to their `timeout` for the first
sample and raise `NotReadyError` (`no_state`), with nothing sent, when none comes.

## Guards are yours: the SDK reports facts

Safety is the firmware's job, command handling is Asimov Edge's job, and a guard is the
script's. The SDK does not refuse a command because of what the robot reports: a latched
fault, an alert, a hot actuator, a low battery or the robot mode never stop `stand()`,
`balance()`, `set_velocity()`, `trajectory()` or `set_joints()`. The firmware decides what
a command does. The one refusal is no live state: those verbs wait up to their `timeout` for
a fresh sample (`no_state`, `stale_state`) and raise `NotReadyError` with nothing sent when
none comes. A closed Robot raises `NotConnectedError`, a lost link `LinkLostError` and a
robot on another protocol version `ProtocolMismatchError`, at once. `balance()` in MOVE and `damp()` are sent at once.

So a script that must not drive a hot, faulted or flat robot writes that rule itself, from
`robot.get_state()`, before it sends:

```python
s = robot.get_state()  # one sample; every fact from it
hot = [j.name for j in s.joints if j.temp is not None and j.temp >= 80]  # DAMP latched at 80 C
low = s.battery is not None and s.battery.soc_percent < 20  # warned below 20 %
if s.faulted or hot or low or any(a.severity <= 1 for a in s.alerts):
    raise SystemExit(f"not driving: {s.mode.name}, faulted {s.faulted}, hot {hot}, low {low}")
```

[examples/guard.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/guard.py) is that guard with
editable limits, an example; the motion examples carry `guard(robot)` commented out, off by
default. Do not expect the SDK to stop you, and do not add such checks to the SDK itself.

```python
robot.preflight("move")  # the same facts as a list: .ok, .problems, .has(code); sends nothing
robot.armed  # True / False / None
```

`Preflight.ok` is false only without live state: blocking codes `not_connected`, `no_state`,
`stale_state` (older than 0.5 s). Information, never acted on by the SDK: `faulted` (the
latched alerts named), `alerts` (any active firmware alert, named), `not_armed` (`"move"`
from STAND). Actions: `"stand"`, `"move"` (`balance`, `set_velocity`), `"trajectory"`
(`trajectory`, `set_joints`).

**Armed:** the firmware reports STAND at once but enters MOVE only after STAND has been held
upright for 0.5 s. `stand()` returns once the robot is armed, so `robot.stand()`,
`robot.balance()`, then `robot.set_velocity(...)` walks. A one-shot `balance()` that reaches
an unarmed robot leaves it in STAND, and `balance()` times out saying so.

**Support the robot before MOVE -> STAND and before DAMP.** STAND has no balance loop, so a
free-standing robot asked to stand from MOVE tips over; DAMP drops a standing robot. Before
either, the robot hangs from its gantry hook or is seated on a stool or bench. The SDK sends
both from any robot mode: asking the person first is the script's job
([examples/rest.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/rest.py) does MOVE -> STAND -> DAMP after asking).

## The verbs

Each verb returns a `Sent`. `stand()`, `balance()` and `damp()` wait for the robot mode they
ask for, and `set_velocity()` for its `duration` (`wait=False` returns once sent). Robot modes
go DAMP -> `stand()` -> STAND -> `balance()` -> MOVE; a velocity sent in STAND also puts an
armed robot in MOVE.

| verb | what it does |
|---|---|
| `stand(*, timeout=10.0, wait=True) -> Sent` | STAND from any robot mode; returns once armed. Holds a pose without a balance loop: from MOVE, support the robot first; never to end a walk. Sent once. |
| `balance(*, timeout=5.0, wait=True) -> Sent` | MOVE at zero velocity: the walking policy balances the robot in place. From an armed STAND: returns once MOVE is reported. In MOVE: sent at once, ends any hold, works from any thread; this is how a walk ends. In DAMP: sent, then `WaitTimeoutError` at once ("the robot is in DAMP: stand() first"). Sent once. |
| `set_velocity(vx=0, vy=0, vyaw=0, *, duration=None, wait=None, hold=True, timeout=None) -> Sent` | walk, sent in any robot mode (in STAND an armed robot enters MOVE; in DAMP nothing moves): `vx` forward m/s, `vy` left m/s, `vyaw` counter-clockwise rad/s. Held (re-sent at 10 Hz) until another verb or `duration` s, then zero is sent. By default blocks until then, so it needs `duration` (else `ValueError`); `wait=False` returns at once and keeps the velocity until the next command (control loops, with a short `duration`). `hold=False`: one packet, nothing re-sent, live state checked once; for your own loop; no `duration`/`wait=True`. Clamped to `Limits()`, the firmware caps 0.4 m/s, 0.4 m/s, 0.8 rad/s; `Sent.clamped` says so. |
| `damp(*, timeout=5.0, wait=True) -> Sent` | every actuator compliant now; a standing robot falls, so support it first; returns once DAMP is reported. Never refused. Not an emergency stop: use the E-Stop in Asimov Manager, or cut power at the battery unit. Sent once. |
| `set_joints(positions, *, duration=2.0, hz=50, wait=True, tolerance=0.05, timeout=None) -> Sent` | smooth (minimum-jerk) move of every joint from the current pose, streaming `trajectory` setpoints at `hz`; returns once reached, then holds the target until another verb. Walking policy off: robot must be supported. |
| `trajectory(positions, *, kp=None, kd=None, timeout=0.0) -> Sent` | the raw joint command: one setpoint per call for your own loop, live state checked once (raises at once on a stale stream; radians, `robot.info.dof` values, firmware order, in the frame `state.joint_pos` reports; the SDK converts the biped's ankle motors to the pitch and roll the firmware reads). Joint control ends with `damp()`, with the robot still supported. |

A verb called mid-hold (including `close()` at the end of a `with`) cuts the walk short. A
script that exits without `close()` still sends the zero at interpreter exit.

## Waits

A wait reads the robot's own state report, not a timer.

```python
robot.wait_until(lambda s: s.joint("L_Knee").pos > 0.5, timeout=10.0) -> State
```

It raises typed errors when the answer cannot come: `WaitTimeoutError` (a `TimeoutError`,
`.last` = last state), `StateStaleError` (stream went quiet), `RobotFaultedError` (the
firmware latched DAMP; checked before your predicate, so a fall is never read as success).
`stand()`, `balance()`, `damp()` and `set_joints(wait=True)` raise the same errors, with
`.sent` set. [examples/wait_until.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/wait_until.py) acts the moment an elbow
passes an angle, while `set_joints(wait=False)` is still moving it.

`robot.get_state()` is the latest sample: `.mode` (`Mode.DAMP|STAND|MOVE`), `.upright`, `.faulted`,
`.yaw` (rad, counter-clockwise, wraps in (-π, π]; `None` without IMU), `.euler`, `.quat`,
`.gyro`, `.gravity`, `.joints`/`.joint("L_Knee").pos`, `.battery` (may be `None`), `.age_s`.
`robot.info`: `dof`, `joint_names`, `capabilities`, `endpoint`. `robot.has("camera")`.

## Safety model

- Nothing in the SDK is an emergency stop. Use the E-Stop in Asimov Manager, or cut power at the battery unit.
- The SDK re-sends a held velocity at 10 Hz and sends zero on `balance()`, and on `close()`,
  the end of a `with` block, a lost link or interpreter exit while it holds a velocity.
- On udp and hybrid, Asimov Edge zeroes velocity 2 s after the last one it received, so a
  crashed script leaves the robot standing in MOVE, not walking. On livekit, Asimov Edge stops
  a held velocity when the SDK sends zero or leaves the room.
- A trajectory not re-sent for 2 s makes Asimov Edge switch the robot to DAMP (the robot folds).
- `LinkLostError` (no state for 2 s): the SDK sends zero and every verb raises until you
  `close()` + `connect()` again.
- `stand()` holds a pose without a balance loop. A free-standing robot asked to STAND after
  walking tips over and latches a fault-DAMP until the firmware restarts; the SDK sends it
  anyway, so a script hangs the robot from its gantry hook or seats it on a stool or bench
  first. End a walk with `balance()`.
- In DAMP the firmware does not walk: `balance()` says "stand() first"; a velocity is sent
  and has no effect.
- **Faults latch.** A fall, actuator over-temperature, battery protection, a joint past its
  limit, lost actuator communication or a watchdog makes the firmware latch DAMP until it
  restarts; `state.faulted` stays True, as it does in robot mode FAULT_DAMP. Motion commands
  are still sent; a wait that sees the latch raises `RobotFaultedError`. Nothing a script sends
  clears it.
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
from menlo.asimov import NotReadyError, Robot, WaitTimeoutError

with Robot().connect() as robot:
    s = robot.get_state()  # your guard: the SDK does not refuse on what the robot reports
    if s.faulted or any(j.temp is not None and j.temp >= 80 for j in s.joints):
        raise SystemExit(f"not walking: robot mode {s.mode.name}, faulted {s.faulted}")
    try:
        robot.stand()  # DAMP -> STAND; returns once armed
        robot.balance()  # STAND -> MOVE; returns once MOVE is reported
        robot.set_velocity(vx=0.3, duration=3.0)  # 0.3 m/s for 3 s, then zero
    except (NotReadyError, WaitTimeoutError) as exc:  # no live state, a fault, not reached
        raise SystemExit(str(exc)) from None
    robot.balance()  # MOVE at zero velocity: the robot balances in place
    # Do not call robot.stand() here: it would stiffen a balancing robot and tip it over.
```

The same steps, split up, are [examples/stand.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/stand.py),
[examples/balance.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/balance.py) and [examples/walk.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/walk.py).
Run from a robot already in MOVE, drop the `stand()`: STAND has no balance loop, and a
free-standing robot tips over.

## Example 2: turn 180° and verify from the IMU

```python
import math
from menlo.asimov import Mode, Robot

RATE = 0.6  # rad/s, under the firmware's 0.8 rad/s cap
SECONDS = math.pi / RATE  # 5.2 s for a half turn, open loop

with Robot().connect() as robot:
    if robot.get_state().mode is Mode.DAMP:
        robot.stand()  # returns once armed
    robot.balance()  # into MOVE, or zero velocity if already there
    before = robot.get_state().yaw
    robot.set_velocity(vyaw=RATE, duration=SECONDS)  # + is counter-clockwise
    after = robot.wait_until(lambda s: s.age_s < 0.2, timeout=2.0).yaw
    turned = math.remainder(after - before, math.tau)  # wrapped difference, in (-π, π]
    print(f"turned {math.degrees(turned):+.0f}° (asked +180°)")
    residual = math.remainder(math.pi - turned, math.tau)  # what is left of the half turn
    if abs(residual) > math.radians(15):  # the gait slips; correct once
        robot.set_velocity(vyaw=math.copysign(RATE, residual), duration=abs(residual) / RATE)
    robot.balance()
```

`yaw` is IMU heading relative to wherever the firmware booted, so only differences mean
anything, and both differences are wrapped with `math.remainder(..., math.tau)`: a 190° turn
reads as `turned = -170°`, and the wrapped residual is then -10° (turn back), not +350°.

More: [examples/README.md](https://github.com/menloresearch/menlo-sdk/blob/main/examples/README.md).

## Gotchas

- **`set_velocity` blocks for its `duration` by default.** Without a `duration` it raises
  `ValueError`; pass `wait=False` in a control loop, where the next verb, or the end of the
  `with` block, cuts the hold short.
- **The SDK does not guard.** A hot joint, a low battery, an alert or a latched fault does
  not stop a command; write the guard from `robot.get_state()` (see examples/guard.py).
- **`stand(wait=False)` then `balance()`** sends the zero velocity as soon as STAND is
  reported, before the 0.5 s upright hold: the robot stays in STAND and `balance()` times
  out. Use `stand()` (the default waits until armed).
- **`connect()` waits for state.** Firmware off: `ConnectError: no state from the robot ...`
  after `timeout` (default 5 s). Media-only work: `connect(require_state=False)`.
- **No state on udp or hybrid.** Asimov Edge needs `udp-control` on and
  `udp-state-host` set to this machine; it sends UDP state to one address.
- **Two scripts, one credential.** Each session gets its own identity
  (`sdk-<credential id>-<host>-<random>`), so two connects coexist. Pass
  `ManagerConfig(label=...)` only if you want a fixed name: two sessions with the same label
  evict each other (LiveKit keys participants by identity).
- **Trajectories need support.** `trajectory()`/`set_joints()` switch off the walking policy.
  Joint control ends with `damp()`, with the robot still supported.
- **Errors:** robot/link errors subclass `MenloError` (`ConnectError`, `NotConnectedError`,
  `LinkLostError`, `UnsupportedError`; `NotReadyError`: no live state, nothing sent,
  `.problems` with codes; `RobotFaultedError`, a `NotReadyError`: a wait saw a latched
  fault; `WaitTimeoutError`:
  sent, but not reached in time; `StateStaleError`); your own mistakes are builtins
  (`ValueError` for a bad speed or duration, `KeyError` for a joint name).
- **Outcomes:** `sent.wait_outcome()` is `Unknown` on every connection mode, because Asimov
  Edge reports no per-command verdict. Read success from `robot.get_state()`, never infer it from
  what was sent.
