# asimov-sdk

Drive an Asimov robot from Python.

```python
from asimov_sdk import Robot, Mode

with Robot.connect_direct("asimov.local") as robot:
    robot.stand()
    robot.wait_for(Mode.STAND, timeout=10.0)
    robot.set_velocity(vx=0.25, duration=4.0)  # m/s, held for 4 s, then zero
    robot.wait_for(Mode.MOVE)
    robot.stand()  # zero velocity is MOVE at rest, not STAND
    robot.wait_for(Mode.STAND)
```

One `Robot`, one API, pluggable transports. The **direct** lane — bare `asimov.io`
protobufs to the robot's edge over the LAN — ships today. The **cloud** lane (through the
Menlo platform) plugs into the same `Transport` seam next; nothing above it changes.

> Status: **alpha**, pre-1.0. The API follows the robot's own wire vocabulary and will
> stay close to it; names may still move before 1.0.

## Install

```bash
uv add "asimov-sdk @ git+https://github.com/menloresearch/asimov-sdk.git"
```

The only runtime dependency is [`asimov-protocol`](https://github.com/menloresearch/asimov-protocol)
(the generated protobufs), pulled by git URL at the tag the edge pins. Both repos are
private; `uv` uses your git credentials (`gh auth login` is enough).

## What you need on the robot

The edge must open its UDP control lane and push state to your machine:

```
asimov-edge --udp-control --udp-state-host <your ip>
```

| direction | port | payload |
|---|---|---|
| you → robot | udp/8850 | `asimov.io.RobotCommand`, one per datagram |
| robot → you | udp/8851 | `asimov.io.RobotState`, one per datagram, at telemetry rate |

Your commands land in the edge's **arbiter** beside every other controller (BLE, cloud,
RF, the manager) and pass the same safety layer: velocity is dropped while the firmware is
DAMPed, STAND is suppressed on a fault-DAMP, and two seconds without a velocity zero-and-
STANDs the robot. The SDK does not bypass any of that; it is a client of it.

No robot handy? The simulator is the same edge and the same firmware:
`menlo-studio up --container --sdk`, then `Robot.connect_direct("127.0.0.1")`.

## The API in one screen

```python
robot = Robot.connect_direct(host)      # returns when the first state sample arrives
robot.info                              # RobotInfo: dof, joint names, protocol version, limits

sent = robot.set_velocity(vx, vy, vyaw, duration=None)   # held at 10 Hz until superseded
sent = robot.stop()                     # zero velocity; firmware stays in MOVE at rest
sent = robot.stand()                    # one-shot posture
sent = robot.damp()                     # one-shot; motors compliant NOW — the emergency stop
sent = robot.trajectory(positions)      # direct joint setpoint, len == info.dof

sent.command, sent.clamped              # what was ACTUALLY sent
sent.outcome                            # Applied | Refused | None (pending); never blocks
sent.wait_outcome(timeout)              # Applied | Refused | Unknown
sent.require()                          # raise CommandRefusedError on Refused

robot.state                             # latest State: mode, joints, gravity, alerts, age_s
robot.wait_for(Mode.STAND, timeout=)    # blocks on the robot's OWN report
robot.wait_until(lambda s: ..., timeout=, stale_after=)
robot.on_refused = callback             # when the edge reports refusals
robot.close()                           # zero velocity if held, then drop the link
```

Two questions are deliberately separate:

- **Was it admitted?** `sent.wait_outcome()` is the arbiter's verdict for that command.
  Today's edge does not report verdicts, so it returns `Unknown`. `Unknown` is never
  treated as success and never as refusal.
- **Did it take effect?** `wait_for` / `wait_until` read the robot's state stream. They
  raise typed errors when the answer cannot come: `StateStale` (the stream went quiet),
  `RobotFaulted` (the firmware fault-DAMPed), `WaitTimedOut`.

Errors that concern the robot or the link subclass `AsimovError`. Caller mistakes stay
builtins: a non-finite velocity is a `ValueError`, an unknown joint name a `KeyError`.

## Safety model, in short

- A velocity is **held** and re-sent at 10 Hz by a background thread. That feeds nothing on
  the robot; it holds off the edge's two-second watchdog, which is the real safety net.
  `duration=` bounds the hold; the SDK sends an explicit zero when it ends.
- **Mode commands are one-shot.** Repeating STAND at 10 Hz would let a script out-shout
  an operator's DAMP.
- `close()` (and the `with` block) sends zero velocity if one is held, then drops the
  link. It never damps: damping a standing biped collapses it.
- `damp()` **is** the emergency stop, and it raises on a dead link like every other verb.
- Speeds are clamped client-side (`Limits`, default 0.6 m/s / 1.5 rad/s) and the clamp is
  visible on `Sent.clamped`.
- Lose the state stream for two seconds and the `Robot` is `LinkLost`: terminal, no
  auto-reconnect (reconnecting would re-latch a velocity across a gap you never saw).

## Layout

```
src/asimov_sdk/
  robot.py          Robot: verbs, hold, waits, error model — transport-neutral
  _command.py       Velocity / ModeCommand / Trajectory, Limits
  _state.py         State, Joint, Alert, Mode, RobotInfo
  _outcome.py       Sent, Applied / Refused / Unknown, Refusal
  _errors.py        AsimovError tree
  robots.py         per-robot tables the wire does not carry yet (joint names, protocol version)
  transport/
    base.py         the Transport protocol every wire implements
    udp.py          the direct lane (asimov-edge UdpConnector)
```

## Develop

```bash
make sync          # uv sync
make check         # ruff + mypy --strict + unit tests
make integration   # the REAL asimov-edge UdpConnector in-process (ASIMOV_EDGE_SRC=<edge>/src)
make live          # a robot or studio rig (ASIMOV_SDK_LIVE_HOST=127.0.0.1)
```

CI runs lint, types and unit tests on 3.12 and 3.13, builds the wheel, and drives the
real edge connector at the commit in `tests/integration/edge.pin`.

## Roadmap

- **Cloud transport** — LiveKit room shared with the edge, `CloudCommand` out,
  `EdgeTelemetry` in; RSL signing for gated robots. Same `Robot`.
- **Outcomes** — the edge reporting per-command verdicts on both lanes; `Sent.wait_outcome`
  stops returning `Unknown`.
- **Discovery** — `robot.info` filled by the robot (model, joints, capabilities) instead of
  a table in `robots.py`.
- **Camera** on the direct lane.

## License

MIT. See `LICENSE`.
