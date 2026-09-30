# menlo-sdk reference

The long form of the [README](../README.md): every connection mode, every verb, the safety model, the errors, and how to develop the SDK itself. An agent writing scripts against the SDK starts from [SKILL.md](SKILL.md). Runnable scripts are in [examples/](../examples/README.md).

## Connection

Save a robot once with `menlo setup` (or `menlo robots add`), then `Robot().connect()` finds it. Or say where the robot is:

```python
from menlo.asimov import ConnectionConfig, ManagerConfig, Robot, UdpConfig

cfg = ConnectionConfig(
    udp=UdpConfig(host="192.168.22.32"),  # the robot's address, for udp and hybrid
    livekit=ManagerConfig(url="http://192.168.22.32", credential=CRED),  # Asimov Manager
)
with Robot(cfg).connect("hybrid") as robot:  # or "udp" / "livekit": same Robot, pick per session
    ...
```

The connection mode is chosen last. Describe how to reach the robot once in a `ConnectionConfig` (one typed class per connection, nothing mixed), bind a
`Robot` to it, then `connect(mode)`; `close()` and `connect()` again to switch connection
modes on the same `Robot`. `connect()` with no mode uses the config's `mode` (a saved
robot's, or `MENLO_MODE`), else the one mode its fields allow; a config that allows several
and names none is a `ValueError`. A mode whose fields are missing raises `ConnectError`
before any network I/O, naming the field and the command that sets it. Commands land in
Asimov Edge's arbiter beside the robot's other controllers and pass the same safety layer,
whichever connection mode they arrive on.

Example: [connect.py](../examples/connect.py) builds the config for each of the three
connection modes side by side.

## Connection modes

| `connect(mode)` | control + state | camera + audio | needs a LiveKit server | config slots |
|---|---|---|---|---|
| `"udp"` | UDP 8850 / 8851 | none | no | `udp=UdpConfig(host)` |
| `"hybrid"` | UDP 8850 / 8851 | LiveKit | yes | `udp=...` and `livekit=...` |
| `"livekit"` | LiveKit data packets / data track | LiveKit | yes | `livekit=...` |

The `livekit` slot is either `ManagerConfig(url, credential)` (the SDK asks Asimov Manager
for the room and a fresh join token on every connect, so you never hold a LiveKit token) or
`LiveKitConfig(url, room, token)` when you run your own LiveKit server.
`cfg.available_modes()` says which modes a config can reach. The Asimov Manager URL is
whatever the robot's web console answers on: `http://192.168.22.32` (port 80),
`http://asimov.local:8080`, or a bare host (`http://` is added); no port is assumed. Asimov
Manager reports the LiveKit URL as the robot sees it, often `ws://localhost:7880`; the SDK
substitutes the manager's host so the room is reachable from your machine.

The verbs, the waits and the errors are the same on all three. Pick `udp` on the robot's network when you need no camera, `hybrid` on the
robot's network when you do, and `livekit` wherever the robot's Asimov Manager is
reachable. State arrives at the firmware's rate (200 Hz) on udp and hybrid, and at 10 Hz on
livekit.

`pip install menlo-sdk` drives every connection mode. `livekit` is imported lazily, in one
module, so `import menlo.asimov` and `connect("udp")` never load it.

### The protocol

```
udp / hybrid      commands -> udp/8850            one bare asimov.io.RobotCommand per datagram
                  state    <- udp/8851            one bare asimov.io.RobotState per datagram
livekit           commands -> data topic "commands"   the SAME RobotCommand bytes, reliable packets
                  state    <- data track "state"      the SAME RobotState bytes, one per frame,
                                                      ordered; user_timestamp = Asimov Edge's clock
hybrid, livekit   camera   <- a video track, decoded to rgb8 Frames
                  mic      <- an audio track, as pcm_s16le AudioChunks
                  speaker  -> an audio track the SDK publishes
```

There is no envelope, framing or type tag: the port, or the topic, says what the bytes are. Each
command carries `protocol_version = 1`, a sequence number and the sender's clock in
`timestamp_us`; neither Asimov Edge nor the firmware checks that clock, and there is no
timestamp window. The scripts in [livekit_raw/](../examples/livekit_raw) speak this protocol
with `livekit` and `asimov-protocol` only.

The SDK never holds a LiveKit API secret. There is no `api_key`/`api_secret` parameter
anywhere in it: a caller brings a join token minted by Asimov Manager, or a callable that
mints a fresh one per join (`token=lambda: fetch()`).

There is no `identity` parameter either. A participant's identity is a claim inside the token
(`sub`), and the LiveKit server ignores whatever a client says about it. The SDK reads it
back instead: `transport.identity`, and `robot.info.endpoint` reads `room@url as <identity>`
once joined. One token is one participant: two participants in a room need two tokens, or
the server disconnects the earlier duplicate. With `ManagerConfig` every session gets its
own identity, `sdk-<credential id>-<host>-<6 random hex>`, so two scripts on one credential
coexist; `ManagerConfig(label="agent")` fixes the suffix when you want a recognisable name,
and two sessions with the same label then evict each other.

## Saved robots and the environment

`Robot()` with no argument (and `ConnectionConfig.from_environment()`) looks, in order:

1. The environment: `MENLO_UDP_HOST` and/or `MENLO_MANAGER_URL` + `MENLO_CREDENTIAL` (one of
   the manager pair without the other is a `ConnectError`). Not merged with a saved robot.
2. A saved robot in `$MENLO_HOME/robots.toml` (default `~/.menlo/robots.toml`): the one named
   by `MENLO_ROBOT` (the CLI's `--robot`), else the file's `default`, else the only one.
3. Otherwise `ConnectError`, naming `menlo setup`, `Robot(cfg)` and the variables.

`MENLO_MODE` then sets the connection mode `connect()` uses with no argument, and
`MENLO_LIMITS` the velocity clamp, whichever source supplied the connection.

```toml
default = "lab"

[robots.lab]
mode = "hybrid"                       # udp | hybrid | livekit
udp_host = "192.168.22.32"            # udp, hybrid
manager_url = "http://192.168.22.32"  # hybrid, livekit
credential = "..."                    # hybrid, livekit
room = "robot-menlo-0001"             # written from Asimov Manager's answer

[robots.lab.limits]                   # optional; missing keys take the firmware caps
vx = 0.3
```

udp needs `udp_host`; hybrid needs `udp_host`, `manager_url` and `credential`; livekit needs
`manager_url` and `credential`. The directory is 0700 and the file 0600: it holds SDK
credentials. `menlo setup`, `menlo robots add`, `connect(persist=True)` and `MENLO_PERSIST=1`
write it; the last two only after a connect that succeeded, and only with a `ManagerConfig`.

| variable | meaning |
|---|---|
| `MENLO_ROBOT` | saved robot name |
| `MENLO_MODE` | `udp`, `hybrid` or `livekit` for `connect()` with no mode |
| `MENLO_UDP_HOST` | the robot's address for udp and hybrid |
| `MENLO_MANAGER_URL` | Asimov Manager URL |
| `MENLO_CREDENTIAL` | SDK credential |
| `MENLO_LIMITS` | `"vx,vy,vyaw"` |
| `MENLO_HOME` | directory of `robots.toml` (default `~/.menlo`) |
| `MENLO_PERSIST` | `1`, `true`, `yes` or `on`: `connect(persist=True)` |

## The command line

```bash
menlo setup                                  # ask, check, save a robot
menlo robots [--json]                        # saved robots, default first
menlo robots add NAME [--mode MODE] [--udp HOST] [--manager URL] [--credential C]
                      [--limits VX,VY,VYAW] [--default] [--no-check] [--no-input]
menlo robots remove NAME | menlo robots use NAME
menlo status [--watch] [--json]              # READY / NOT READY / FAULTED; sends nothing
menlo stand [-y]                             # DAMP -> STAND, then waits until armed
menlo walk --vx 0.3 --duration 3 [-y]        # 0 < duration <= 10 s, then stop()
menlo stop                                   # zero velocity, only in MOVE; never asks
menlo damp [-y]                              # every actuator compliant; not an emergency stop
```

`--robot NAME` and `--mode udp|hybrid|livekit` work with every command.

`stand`, `walk` and `damp` print a plan on one line and ask `Proceed? [y/N]` before they
send anything:

```text
lab (udp, 192.168.22.32) · DAMP · battery 82 % → stand, then wait until armed
lab (hybrid, 192.168.22.32) · STAND, armed · battery 82 % → walk vx 0.30 m/s, vy 0.00 m/s, vyaw 0.00 rad/s for 3.0 s, then stop
lab (udp, 192.168.22.32) · MOVE · battery 82 % → damp: every actuator goes limp and a standing robot folds, so the robot must be supported. Not an emergency stop: use the E-Stop in Asimov Manager, or cut power at the battery unit.
```

A speed above the limits shows the value that is sent, then the one asked for:
`vx 0.40 m/s (asked 0.60)`. Only `y` or `yes` goes ahead; Enter or anything else cancels
(exit `4`, nothing sent). `-y`/`--yes` prints the plan and goes ahead without asking. With no
terminal and no `--yes`, the command sends nothing and exits `2`. `stop` never asks.

Before the plan, `stand` runs `preflight("stand")` and `walk` runs `preflight("move")`
(waiting up to 5 s for a robot in STAND to be seen armed). A blocking problem prints
`Not feasible:`, the robot mode, the reason and the fix, on stderr, and exits `3` without
asking:

```text
Not feasible: lab is in DAMP, not balancing. Run `menlo stand` first.
Not feasible: lab is in STAND, not armed. STAND has not been held upright for 0.5 s; the firmware accepts MOVE after that. Run the command again once `menlo status` shows it armed.
Not feasible: lab is in MOVE, balancing. `stand` only runs from DAMP; end a walk with `menlo stop`.
Not feasible: lab is in DAMP, faulted. The firmware latched DAMP (FALL_DETECTED); it stays latched until the firmware restarts.
Not feasible: no fresh state from lab: the latest state is 1.2 s old (limit 0.5 s). Check the link with `menlo status`.
```

`walk` runs `preflight("move")` again after the answer; if the robot changed, it prints
`Not feasible:` and sends nothing. `damp` is not gated by preflight. `stand` on a robot
already in STAND sends nothing and exits `0`.

A fault during a walk ends it: `walk` names the alert, says it latches until the firmware
restarts, and exits `3`.

Exit codes: `0` done, `1` error, `2` usage, `3` not feasible (or the robot did not become
ready, or a fault ended a walk), `4` cancelled, `130` interrupted. An agent runs `menlo status --json` first, then
`menlo walk ... --yes`, and branches on the exit code.

## Install

Python 3.12 or newer.

```bash
uv add menlo-sdk                    # from PyPI; the robot is `menlo.asimov`

# an unreleased commit, straight from git:
uv add "menlo-sdk @ git+https://github.com/menloresearch/menlo-sdk.git"
```

Releases are the `v*` tags of this repo, each with a GitHub Release carrying the files that were
uploaded to PyPI; how versions are chosen and cut is in [RELEASING.md](../RELEASING.md).

Runtime dependencies: `asimov-protocol` (the generated `asimov.io` bindings;
`>=1.2.1rc1,<2`, the bound following the protocol directory `v1/`), `protobuf`, `livekit` (the
hybrid and livekit connection modes), and `questionary` and `rich` (the `menlo` command
line). There are no extras. numpy, Pillow and OpenCV are
optional: the calls that need one name it in the error.

## The robot side

For `udp` and `hybrid`, Asimov Edge must have `udp-control` on and send state to your
machine (`udp-state-host` set to your IP); both are Asimov Edge parameters in Asimov
Manager. Asimov Edge sends UDP state to one address, so one machine at a time receives it.

For `hybrid` and `livekit`, Asimov Edge joins a LiveKit room (one per robot, named by
its serial), publishes the camera and microphone as ordinary tracks, and (on livekit)
answers on the `state` data track. With `ManagerConfig` the SDK gets the room name and a
token from Asimov Manager itself. The SDK credential comes from the Developer page of Asimov Manager;
its role is `control` (may drive over livekit and use the speaker) or `observe` (may watch).
The role applies to the room only: hybrid sends commands over UDP, which has no sign-in. An
agent framework can join the same room and subscribe the robot's tracks itself;
[apps/agent_room.py](../examples/apps/agent_room.py)
shows the SDK half.

**Control priority.** Asimov Edge executes one source at a time: the Asimov Manager Cockpit,
then a paired gamepad, then udp, then livekit. A paired gamepad holds control even when
idle. The state stream does not show who holds control, so a velocity that another source
outranks has no effect and nothing reports it.

## API in one screen

```python
robot = Robot()  # the environment, else the saved robot (see above)
cfg = ConnectionConfig(
    udp=UdpConfig(host, command_port=8850, state_bind=("0.0.0.0", 8851), state_source=None),
    livekit=ManagerConfig(url="http://host", credential=CRED, label=None),
    # or: livekit=LiveKitConfig(url="ws://host:7880", room="robot-<serial>", token=str_or_callable)
    limits=None,  # used when Robot(limits=) is not given
    mode=None,  # what connect() uses with no argument
)
cfg.available_modes()  # ("udp", "hybrid", "livekit")
ConnectionConfig.from_environment(robot="lab")  # a saved robot by name
robot = Robot(cfg, limits=None, link_timeout=2.0)  # bound; nothing touches the network
robot.connect("hybrid", timeout=5.0, media_timeout=3.0, connect_timeout=10.0)  # returns robot
robot.connect()  # the config's mode, else its one mode; ValueError when it allows several
robot.connect("livekit", require_state=False)  # media now; state and verbs once the robot reports
robot.connect("livekit", persist=True)  # save the connection after it succeeds
robot.close()
robot.connect("udp")  # switch connection modes on the same Robot
robot = Robot(transport, limits=None, link_timeout=2.0)
robot.open()  # any Transport

# readiness: sends nothing
robot.preflight("move")  # Preflight(ok, problems, ...); "stand" | "move" | "trajectory"
robot.wait_ready("move", timeout=5.0)  # blocks until ok; NotReadyError otherwise
robot.armed  # True / False / None: does the firmware accept MOVE now?

# verbs: each returns a Sent immediately; the protocol's own vocabulary
robot.set_velocity(vx, vy, vyaw, duration=None, wait=False)  # held at 10 Hz until superseded
robot.set_velocity(vx=0.25, duration=1.0, wait=True)  # blocks until the hold ends and zero is sent
robot.stop()  # zero velocity; stays in MOVE, balancing: how a walk ends
robot.stand()  # one-shot; stiffen to a pose, no balance loop; see the safety model
robot.damp()  # one-shot; every actuator compliant now; not an emergency stop
robot.trajectory(positions, kp=None, kd=None)  # one setpoint, radians, firmware order
robot.goto(positions, duration=2.0, hz=50, wait=True, tolerance=0.05)  # from the current pose

# waits: the robot's own report, never a sleep
robot.wait_for(Mode.STAND, timeout=10, stale_after=None)
robot.wait_until(lambda s: s.upright and s.mode is Mode.MOVE, timeout=10, stale_after=None)

# state
s = robot.state  # latest sample: mode, joints, gravity, gyro, quat, euler, alerts, battery
s.age_s
s.upright  # gravity z below -0.8; not the arming test (use robot.armed)
s.faulted  # error_flags set or a critical alert; stays True until the firmware restarts
s.yaw  # heading, rad, counter-clockwise; a turn is math.remainder(after - before, math.tau)
s.joint("L_Knee").pos
s.battery.soc_percent  # s.battery is None when the robot reports none
robot.info  # transport, endpoint, dof, joint_names, protocol_version, limits, capabilities
robot.has("camera")
robot.require("drive", "battery")

# media: UnsupportedError when this robot, over this connection, does not carry it
robot.camera.photo(timeout=5)  # one fresh rgb8 Frame; WaitTimeoutError if the camera is quiet
robot.camera.photo().to_jpeg(quality=85)  # bytes; needs Pillow, else ImportError naming it
robot.camera.latest()
robot.camera.frames(timeout=5)  # latest-wins iterator
robot.camera.subscribe(cb)
clip = robot.camera.capture_clip(5.0, audio=True)  # Clip(frames, audio, started_at)
clip.save_wav("clip.wav")  # stdlib wave
clip.frames_as_numpy()  # (n, h, w, 3); needs numpy
clip.save_frames("out/")  # JPEG; needs Pillow, else ImportError naming it
clip.save_mp4("clip.mp4")  # needs opencv-python
robot.microphone.chunks()  # ordered, bounded; robot.microphone.dropped counts the losses
robot.speaker.play_pcm(pcm_s16le, sample_rate_hz=16000)

# callbacks (transport thread; keep them short)
robot.on_state = ...
robot.on_alert = ...
robot.on_mode_change = ...
robot.on_link_lost = ...

# recording
with robot.record("run.jsonl"):
    ...  # every state sample and every command, JSON lines; menlo.asimov.recording.load() reads it

# outcomes: was the command admitted? separate from "did it take effect"
sent = robot.stop()
sent.wait_outcome()  # Unknown on every connection mode: Asimov Edge reports no verdict
```

## Check before you move

`robot.preflight(action)` reads the latest state and returns a `Preflight`: `ok` when no
problem is blocking, `problems` (blocking first, then warnings), `has(code)`, and a readable
`str()`. It sends nothing and never raises for a robot condition. `action` is `"stand"`,
`"move"` (`set_velocity`) or `"trajectory"` (`trajectory` and `goto`). `stand` is ok from
DAMP or STAND; `move` and `trajectory` are ok in MOVE or in an armed STAND. `str()` reads
"ready to stand", "not ready to move:" or "not ready to run a trajectory:", then one line
per problem.

| code | blocking | when |
|---|---|---|
| `not_connected` | yes | the Robot is closed, its link was lost, or the protocol version differs |
| `no_state` | yes | the session is open and the robot has not reported state |
| `stale_state` | yes | the latest state is older than 0.5 s |
| `faulted` | yes | the firmware latched DAMP; it stays so until the firmware restarts |
| `battery_protecting` | yes | the battery management system is protecting the pack |
| `battery_low` | yes | state of charge below 20 % |
| `unknown_battery` | no | the robot reports no battery |
| `joint_hot` | yes | an actuator at or above 60 C |
| `unknown_joint_temp` | no | the robot reports no actuator temperatures |
| `wrong_mode` | yes | stand from MOVE; move or trajectory from DAMP (not reported with `faulted`: standing does not clear a latch) |
| `not_armed` | yes | STAND has not been held upright for 0.5 s |
| `unknown_gravity` | no | no gravity vector, so tilt and arming cannot be checked; `move` and `trajectory` then pass in STAND unverified |

`robot.wait_ready(action, timeout=5.0)` polls until the check is ok and returns it. It
raises `NotReadyError` (carrying `.preflight`) after `timeout`, or at once on `faulted` or
`not_connected`, which waiting cannot clear.

**Armed.** The firmware reports STAND at once but accepts MOVE only once STAND has been held
upright (gravity z below -0.87) for 0.5 s. A velocity sent before that is neither refused
nor reported: the robot stays in STAND while a `duration` runs. Call `wait_ready("move")`
after `stand()` and before the first `set_velocity`. See
[check.py](../examples/check.py), [stand.py](../examples/stand.py) and
[walk.py](../examples/walk.py).

## Safety model

- **Nothing in the SDK is an emergency stop.** Use the E-Stop in Asimov Manager, or cut power at the battery unit.
- **Velocity limits.** The Motion Control Board firmware caps velocity at 0.4 m/s forward
  and sideways and 0.8 rad/s turning. `Limits()` defaults to those caps, so `Sent.clamped`
  is true exactly when the robot would not walk at the speed asked for. Set lower limits
  with `Robot(limits=Limits(...))`, `[robots.NAME.limits]` or `MENLO_LIMITS="vx,vy,vyaw"`;
  higher values are sent as asked and the firmware clamps them. `Limits` rejects negative
  or non-finite values.
- **A held velocity is re-sent at 10 Hz.** On udp and hybrid, Asimov Edge zeroes velocity
  2 s after the last one it received; the robot then stays in MOVE, balancing. On livekit,
  Asimov Edge stops a held velocity when the SDK sends zero or leaves the room.
- **The SDK sends zero** on `stop()`, and on `close()`, the end of a `with` block or a lost
  link while it holds a velocity.
  When the robot latches a fault or its firmware restarts, the SDK releases the hold and
  sends nothing more. `duration=` bounds a hold on the client; the SDK sends the zero itself when time
  is up. `set_velocity` returns at once either way: the next verb, or `close()` at the end
  of a `with` block, cuts an unexpired hold short. `wait=True` blocks until the hold has
  ended and its zero has gone out.
- `close()` sends a zero if a velocity was held and stops re-sending a held trajectory.
  `LinkLostError` (no state for `link_timeout` seconds) does the same, then every verb raises
  until you `close()` and `connect()` again. Asimov Edge puts the robot in DAMP 2 s after
  the last trajectory setpoint; there is no neutral setpoint the SDK could send instead.
- A verb is never dropped as superseded; only the keepalive's re-sends are. A new verb ends
  any running `goto()`.
- **MOVE from STAND only once armed** (see Check before you move). In DAMP, Asimov Edge drops
  velocities and trajectories; nothing raises.
- `trajectory()` and `goto()` put every joint under position control with the walking
  policy off: the robot does not balance itself while one is in force. On a standing biped
  use them with the robot supported, or with gains known to hold the legs. Without `kp`/`kd`,
  Asimov Edge applies its own per-joint gain table. Positions are in the frame
  `state.joint_pos` reports. On the biped each ankle is reported as its two motors (A, B),
  and the SDK sends those as the ankle pitch and roll the firmware reads there. The firmware
  limits ankle pitch to 0.35 rad and roll to 0.1 rad, so a trajectory of the reported pose
  holds the robot still when both ankles are within those limits, as they are standing.
  `trajectory()` and `goto()` raise `ValueError` for an ankle target more than 0.02 rad past
  a limit, before anything is sent. `goto()` holds its target until another
  verb; `trajectory()` is one setpoint you clock yourself. Joint control ends in DAMP, with
  the robot still supported: call `damp()`, or stop sending and Asimov Edge puts the robot in
  DAMP 2 s after the last setpoint, as [move_joints.py](../examples/move_joints.py) does.
- **`stand()` holds a pose without a balance loop.** It blends every joint to a fixed pose and
  holds it with position gains, with no balance loop. It is the verb that wakes the robot
  (DAMP, then STAND, then MOVE): call it only from DAMP, after `wait_ready("stand")`.
  Asking a free-standing biped to stiffen after walking tips it over. To stand still
  after a walk, call `stop()` and stay in MOVE at zero velocity, where the policy keeps
  balancing. [walk.py](../examples/walk.py) ends a walk this way.
- **The last command in effect wins; `goto()` yields to any other verb.** The firmware obeys
  whichever command arrived last. A `goto()` is fenced by the SDK: any verb from any
  thread (`damp()`, `stand()`, a velocity) ends it before its next setpoint leaves.
  A loop you clock yourself with `trajectory()` is not: a verb sent from another thread
  can be overwritten by your next setpoint. Stop your loop, then send the verb.
- `damp()` makes every actuator compliant and a standing biped folds. The SDK sends it only
  when you call it.
- **Faults latch.** A critical alert (a fall, actuator over-temperature, battery protection,
  a joint past its limit, lost actuator communication, a watchdog) makes the firmware latch
  DAMP until it restarts. `error_flags` stay set and `state.faulted` stays True until then;
  `preflight()` reports `faulted`, `wait_ready()` raises `NotReadyError` with the code
  `faulted`, and `wait_for()` and `wait_until()` raise `RobotFaultedError`. Nothing a script
  sends clears it.
- **Outcomes are Unknown.** Asimov Edge reports no per-command verdict, so
  `sent.wait_outcome()` is `Unknown` on every connection mode and `on_refused` never fires.
  Read the effect from `robot.state`.
- A capability is claimed from a track that arrived. A LiveKit room publishing no video
  makes `robot.has("camera")` False and `robot.camera` raise `UnsupportedError`, rather
  than hand out a stream that never yields. `media_timeout=` bounds the wait and returns as
  soon as the video track is up, so a robot with a camera and no microphone does not pay
  the whole budget. A track that lands after the connect still attaches and still works; it
  misses `robot.info.capabilities`, which is a snapshot; raise `media_timeout` when a late
  track must be reflected there.
- The state port is plain UDP: samples that do not look like this robot are dropped, an
  older datagram never overwrites a newer sample, and `state_source=` pins the one address
  state may arrive from.
- UDP commands are not authenticated: any host on the robot's network can send them.
  Keep udp and hybrid on a network you trust.

## Errors

| Exception | When |
|---|---|
| `ConnectError` / `ProtocolMismatchError` | a missing field for the mode; no state within `timeout`; protocol version differs |
| `NotConnectedError` | a call before `connect()` or after `close()`; `state`/verbs in a `require_state=False` session before the robot reported |
| `NotReadyError` | `wait_ready()` timed out, or the robot is faulted or not connected; `.preflight` has the problems |
| `LinkLostError` | no state for `link_timeout` s; the session is over |
| `WaitTimeoutError` (also `TimeoutError`) | a wait's condition was not met in time; `.last` is the last state |
| `StateStaleError` | the stream went quiet during a wait |
| `RobotFaultedError` | the firmware latched DAMP; `.state` carries the alerts |
| `CommandRefusedError` / `OutcomeUnknownError` | from `Sent.require()` |
| `UnsupportedError` | this robot, over this connection, does not provide the capability |

Caller mistakes stay builtins: `ValueError` for a non-finite velocity, a bad limit or
duration, a trajectory of the wrong length, or an unknown `MENLO_MODE`; `KeyError` for an
unknown joint name.

## Development

To run the checks on your machine, install [uv](https://docs.astral.sh/uv/) and run, from
the repository root:

```bash
make sync          # install the SDK and the development tools
make check         # ruff, mypy --strict (the SDK and the examples) and the unit tests
make live          # against a robot; MENLO_SDK_LIVE_HOST=<host>
make livekit       # against `livekit-server --dev`; needs MENLO_SDK_LIVEKIT_URL
                   # plus two tokens for one room (_TOKEN and _EDGE_TOKEN)
```

`make check` needs no robot: the unit tests run the SDK against the test suite's stand-in
for the robot, which runs in the test process and speaks the same protocol as the robot.
They run every example this way. The interactive ones (`keyboard.py` and the apps) get a
scripted key source, and the `livekit_raw/` scripts that only read a room are skipped: they
need a LiveKit server. CI runs the same checks on Python 3.12 and 3.13 and builds the wheel.

```
src/menlo/
  __init__.py       __version__; one subpackage per robot
  cli/              the `menlo` console script: setup, robots, status, stand, walk, stop, damp
src/menlo/asimov/   the Asimov biped
  robot.py          Robot: verbs, waits, preflight, keepalive, callbacks, goto
  _preflight.py     Preflight, Problem, the problem codes and thresholds
  connection.py     ConnectionConfig, UdpConfig, LiveKitConfig, ManagerConfig
  store.py          ~/.menlo/robots.toml, the zero-config lookup, persist
  _state.py         State, Joint, Alert, Battery, RobotInfo, Mode
  _command.py       Velocity, ModeCommand, Trajectory, Limits
  _outcome.py       Sent, Applied, Refused, Unknown, Refusal
  _media.py         Frame, AudioChunk, Camera, Microphone, Speaker
  recording.py      JSON-lines recording and load()
  _errors.py        the exception taxonomy
  robots.py         per-robot tables: joint names, protocol version
  transport/        Transport protocol, UdpTransport, LiveKitTransport, HybridTransport,
                    _wire.py (the protobufs every connection mode shares) and
                    _livekit_client.py (the one module that imports livekit, lazily)
examples/           runnable scripts, livekit_raw/ and apps/; indexed in examples/README.md
docs/SKILL.md       the two-page reference for an agent writing a script against this SDK
```
