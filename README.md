# asimov-sdk

Drive an Asimov robot from Python.

```python
from asimov_sdk import Mode, Robot

with Robot.connect("asimov.local") as robot:
    robot.stand()
    robot.wait_for(Mode.STAND, timeout=15)
    robot.set_velocity(vx=0.25, duration=4.0)  # m/s, held for 4 s, then zero
    robot.wait_for(Mode.MOVE)
    robot.stand()  # zero velocity is MOVE at rest, not STAND
    print(robot.state.joint("L_Knee").pos, robot.state.battery)  # battery is None without a BMS
```

One `Robot`, one API, pluggable transports. `UdpTransport` speaks the robot's LAN lane:
`asimov.io.RobotCommand` datagrams to the robot's edge on udp/8850, `asimov.io.RobotState`
pushed back on udp/8851. Commands land in the edge's arbiter beside the robot's other
controllers and pass the same safety layer.

## Install

Python 3.12 or newer.

```bash
uv add "asimov-sdk @ git+https://github.com/menloresearch/asimov-sdk.git"
# or: pip install "asimov-sdk @ git+https://github.com/menloresearch/asimov-sdk.git"
```

The only runtime dependency is `protobuf`. The generated `asimov.io` bindings ship inside
the package, pinned to an `asimov-protocol` tag (`src/asimov_sdk/_vendor/VENDORED.md`). If
the `asimov-protocol` package is installed as well and is the same release, the SDK uses
that copy so one process holds one set of descriptors.

## The robot side

The edge must run with `--udp-control` and push state to your machine
(`--udp-state-host <your ip>`). To run against a simulated robot instead of hardware:
`menlo-studio up --container --sdk`, then `Robot.connect("127.0.0.1")`.

## API in one screen

```python
robot = Robot.connect(
    host,
    command_port=8850,
    state_bind=("0.0.0.0", 8851),
    timeout=5.0,
    limits=None,
    state_source=None,
    link_timeout=2.0,
)
robot = Robot(transport, limits=None, link_timeout=2.0)
robot.open()  # any Transport

# verbs — each returns a Sent immediately; the wire's own vocabulary
robot.set_velocity(vx, vy, vyaw, duration=None)  # held at 10 Hz until superseded/stop/duration
robot.stop()  # zero velocity; robot stays in MOVE at rest
robot.stand()  # one-shot
robot.damp()  # one-shot; motors compliant NOW — the emergency verb
robot.trajectory(positions, kp=None, kd=None)  # one setpoint, radians, firmware order
robot.goto(positions, duration=2.0, hz=50, wait=True)  # clocked, interpolated from the current pose

# waits — the robot's own report, never a sleep
robot.wait_for(Mode.STAND, timeout=10, stale_after=None)
robot.wait_until(lambda s: s.upright and s.mode is Mode.MOVE, timeout=10, stale_after=None)

# state
s = robot.state  # latest sample: mode, joints, gravity, gyro, quat, euler, alerts, battery
s.age_s
s.upright
s.faulted
s.joint("L_Knee").pos
s.battery.soc_percent
robot.info  # transport, endpoint, dof, joint_names, protocol_version, limits, capabilities
robot.has("camera")
robot.require("drive", "battery")

# media — UnsupportedError when this robot/transport does not carry it
robot.camera.latest()
robot.camera.frames(timeout=5)
robot.camera.subscribe(cb)
robot.microphone.chunks()
robot.speaker.play_pcm(pcm_s16le, sample_rate_hz=16000)

# callbacks (transport thread; keep them short)
robot.on_state = ...
robot.on_alert = ...
robot.on_mode_change = ...
robot.on_refused = ...
robot.on_link_lost = ...

# recording
with robot.record("run.jsonl"):
    ...  # every state sample and every command, JSON lines

# outcomes — was the command admitted? separate from "did it take effect"
sent = robot.stand()
sent.wait_outcome()  # Applied | Refused | Unknown
sent.require()  # raises CommandRefusedError on Refused
```

## Safety model

- A held velocity is re-sent at 10 Hz. The edge zeroes velocity two seconds after the last
  one it received; the robot then stands in place in MOVE.
- `duration=` bounds a hold on the client; the SDK sends the zero itself when time is up.
- `close()` sends a zero if a velocity was held and stops re-sending a held trajectory.
  `LinkLostError` (no state for `link_timeout` seconds) does the same, then every verb raises
  until you `close()` and `open()` again. A trajectory that is no longer re-sent is DAMPed by
  the edge two seconds later; there is no neutral setpoint the SDK could send instead.
- A verb is never dropped as superseded; only the keepalive's re-sends are. A new verb ends
  any running `goto()`.
- `trajectory()` and `goto()` put every joint under position control with the walking
  policy off: the robot does not balance itself while one is in force. On a standing biped
  use them with the robot supported, or with gains known to hold the legs. The edge DAMPs a
  trajectory two seconds after the last setpoint; `goto()` holds its target until another
  verb, `trajectory()` is one setpoint you clock yourself.
- `damp()` folds a standing biped. It is deliberate and never implied by anything else.
- Speeds are clamped client-side (`Limits`, default 0.6 m/s / 1.5 rad/s), the clamp is
  visible on `Sent.clamped`, and `Limits` rejects negative or non-finite values.
- The state port is plain UDP: samples that do not look like this robot are dropped, an
  older datagram never overwrites a newer sample, and `state_source=` pins the one address
  state may arrive from.
- The robot's fault latch outlives the alert that raised it: after a fall the robot stays
  DAMPed and refuses STAND until its firmware restarts, while `state.faulted` clears after
  about 2.5 s. A `wait_for(Mode.STAND)` in that condition ends in `WaitTimeoutError`.

## Errors

| Exception | When |
|---|---|
| `ConnectError` / `ProtocolMismatchError` | no state within `timeout`; protocol version differs |
| `NotConnectedError` | a call before `open()` or after `close()` |
| `LinkLostError` | no state for `link_timeout` s; the session is over |
| `WaitTimeoutError` (also `TimeoutError`) | a wait's condition was not met in time; `.last` is the last state |
| `StateStaleError` | the stream went quiet during a wait |
| `RobotFaultedError` | the firmware fault-DAMPed; `.state` carries the alerts |
| `CommandRefusedError` / `OutcomeUnknownError` | from `Sent.require()` |
| `UnsupportedError` | this robot, over this transport, does not provide the capability |

Caller mistakes stay builtins: `ValueError` for a non-finite velocity, a bad limit or
duration, or a trajectory of the wrong length; `KeyError` for an unknown joint name.

## Development

```bash
make sync          # uv sync
make check         # ruff, mypy --strict, unit tests (fake edge on the real wire)
make integration   # the real asimov-edge UdpConnector in-process; ASIMOV_EDGE_SRC=<edge>/src
make live          # a robot or simulator; ASIMOV_SDK_LIVE_HOST=<host>
make check-vendor  # vendored bindings match the pinned asimov-protocol tag
make vendor-protocol REF=v1.1.0
```

CI runs the checks on Python 3.12 and 3.13 and builds the wheel. Two jobs need read access
to other menloresearch repositories and skip with a warning when the repository secret is
absent: the real-edge integration job and the vendored-bindings check.

```
src/asimov_sdk/
  robot.py          Robot: verbs, waits, keepalive, callbacks, goto
  _state.py         State, Joint, Alert, Battery, RobotInfo, Mode
  _command.py       Velocity, ModeCommand, Trajectory, Limits
  _outcome.py       Sent, Applied, Refused, Unknown, Refusal
  _media.py         Frame, AudioChunk, Camera, Microphone, Speaker
  recording.py      JSON-lines recording and load()
  _errors.py        the exception taxonomy
  robots.py         per-robot tables: joint names, protocol version
  transport/        Transport protocol and UdpTransport
  _vendor/          generated asimov.io bindings at the pinned tag
examples/           runnable scripts; examples/demos/ are the three walkthroughs
```

## License

MIT. The vendored bindings are generated from `menloresearch/asimov-protocol` and carry
that repository's terms.
