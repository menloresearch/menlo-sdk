# menlo-sdk reference

The long form of the [README](../README.md): every connection mode, every verb, the safety model, the errors, and how to develop the SDK itself. An agent writing scripts against the SDK starts from the [menlo-sdk skill](../skills/menlo-sdk/SKILL.md). Runnable scripts are in [examples/](../examples/README.md).

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
robot's, or `MENLO_MODE`), else the mode its fields imply: `udp` alone is `udp`, `livekit`
alone is `livekit`, and both are `hybrid`; `connect("udp")` or `connect("livekit")` picks one
of the two on a config that has both. A mode whose fields are missing raises `ConnectError`
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
menlo stand [-y]                             # STAND from any mode; returns once armed
menlo balance [-y]                           # STAND -> MOVE (asks); in MOVE, zero velocity at once
menlo walk --vx 0.3 --duration 3 [-y]        # 0 < duration <= 10 s, then balances
menlo damp [-y]                              # every actuator compliant; not an emergency stop
```

`--robot NAME` and `--mode udp|hybrid|livekit` work with every command.

`stand`, `walk`, `damp` and `balance` outside MOVE print a plan on one line, with the
robot's facts (robot mode, armed, faults, active alerts, hottest joint, battery), and ask
`Proceed? [y/N]` before they send anything:

```text
lab (udp, 192.168.22.32) · DAMP · faults none · alerts none · hottest joint 38 C (L_Knee) · battery 82 % → stand, then wait until armed
lab (udp, 192.168.22.32) · STAND · armed · faults none · alerts none · hottest joint 41 C (L_Knee) · battery 82 % → balance: MOVE at zero velocity, the walking policy balances the robot
lab (hybrid, 192.168.22.32) · MOVE · armed · faults none · alerts MOTOR_TEMP_HIGH · hottest joint 62 C (R_Knee) · battery 81 % → walk vx 0.30 m/s, vy 0.00 m/s, vyaw 0.00 rad/s for 3.0 s, then balance in place
lab (udp, 192.168.22.32) · MOVE · armed · faults none · alerts none · hottest joint 45 C (L_Knee) · battery 80 % → damp: every actuator stops holding its position and a standing robot falls, so the robot must be supported. Not an emergency stop: use the E-Stop in Asimov Manager, or cut power at the battery unit.
```

The facts are for you to judge: no command refuses because of them, and the firmware decides
what a command does. A speed above the limits shows the value that is sent, then the one
asked for: `vx 0.40 m/s (asked 0.60)`. Only `y` or `yes` goes ahead; Enter or anything else
cancels (exit `4`, nothing sent). `-y`/`--yes` prints the plan and goes ahead without asking.
With no terminal and no `--yes`, the command sends nothing and exits `2`. `balance` in MOVE
never asks: it sends zero velocity at once, as the way to end a walk.

`stand` from MOVE prints a warning before the plan: STAND has no balance loop, so hang the
robot from its gantry hook or seat it on a stool or bench first. `stand` on a robot already
in STAND sends nothing: armed, it exits `0`; not armed, it waits up to 10 s for the robot to
arm and exits `3` when it does not.

The one refusal is no live state. Before the plan, each command checks for a fresh state
sample, and without one prints `Not feasible:` on stderr and exits `3` without asking:

```text
Not feasible: no fresh state from lab: the latest state is 1.2 s old (limit 0.5 s). Check the link with `menlo status`.
```

A command that was sent and did not get there also exits `3`, with what the robot reports:
`balance` in DAMP ("the robot is in DAMP: stand() first"), `balance` on a robot that has
not armed, `walk` that left the robot outside MOVE ("Run `menlo balance` first"), a `stand`
that does not arm within 10 s, and a fault during a command (`walk` names the alert and says
it latches until the firmware restarts). Ctrl-C during a walk sends zero velocity
(`balance()`) before it exits `130`.

Exit codes: `0` done, `1` error, `2` usage, `3` not feasible (no live state, nothing sent)
or not reached (sent, and the robot did not get there), `4` cancelled, `130` interrupted. An
agent runs `menlo status --json` first, judges the facts, then runs `menlo balance --yes` and
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
agent framework can join the same room and subscribe the robot's tracks itself.

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
robot.connect()  # the config's mode, else what it implies: udp, livekit, or both is hybrid
robot.connect("livekit", require_state=False)  # media now; state and verbs once the robot reports
robot.connect("livekit", persist=True)  # save the connection after it succeeds
robot.close()
robot.connect("udp")  # switch connection modes on the same Robot
robot = Robot(transport, limits=None, link_timeout=2.0)
robot.open()  # any Transport

# facts: what the robot reports, for your own guard; sends nothing, never waits
robot.preflight("move")  # Preflight(ok, problems, ...); "stand" | "move" | "trajectory"
robot.armed  # True / False / None: does the firmware accept MOVE now?

# verbs: each returns a Sent, in any robot mode; the firmware decides
# refused only without live state: NotReadyError (nothing sent) after timeout
robot.stand(timeout=10.0, wait=True)  # STAND: a held pose, no balancing; returns once armed
robot.balance(timeout=5.0, wait=True)  # STAND -> MOVE; returns once MOVE is reported
robot.set_velocity(vx, vy, vyaw, duration=3.0)  # held at 10 Hz, returns once zero is sent
robot.set_velocity(vx=0.25, duration=0.3, wait=False)  # returns at once: a control loop's tick
robot.set_velocity(vx=0.25, hold=False)  # one packet, nothing re-sent: your loop is the clock
robot.set_joints(positions, duration=2.0, hz=50, wait=True, tolerance=0.05, timeout=None)
robot.trajectory(positions, kp=None, kd=None, timeout=0.0)  # one raw setpoint; checked once
# sent at once, without the live-state check
robot.balance()  # in MOVE: ends any hold, zero velocity; the robot balances in place
robot.damp(timeout=5.0, wait=True)  # DAMP: actuators stop holding; returns once DAMP is reported

# waits: the robot's own report, never a sleep
robot.wait_until(lambda s: s.joint("L_Knee").pos > 0.5, timeout=10, stale_after=None)  # -> State

# state
s = robot.get_state()  # latest sample: mode, joints, gravity, gyro, quat, euler, alerts, battery
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
robot.speaker.play_pcm(pcm_s16le, sample_rate_hz=16000)  # blocks until the room has taken it
# examples/record_audio.py writes the microphone to a WAV file; examples/play_audio.py plays one

# callbacks (transport thread; keep them short). damp() in one sends and returns;
# the other waits need wait=False there, or they raise RuntimeError
robot.on_state = ...
robot.on_alert = ...
robot.on_mode_change = ...
robot.on_link_lost = ...

# recording
with robot.record("run.jsonl"):
    ...  # every state sample and every command, JSON lines; menlo.asimov.recording.load() reads it

# outcomes: was the command admitted? separate from "did it take effect"
sent = robot.balance()
sent.wait_outcome()  # Unknown on every connection mode: Asimov Edge reports no verdict
```

## Guards are yours: the SDK reports facts

Safety is the firmware's job, command handling is Asimov Edge's job, and a guard is your
script's. The SDK does not refuse a command because of what the robot reports: its robot
mode, a latched fault, an alert, an actuator temperature or the battery never stop
`stand()`, `balance()`, `set_velocity()`, `trajectory()` or `set_joints()`, and the firmware
decides what a command does. The firmware warns at 60 C and latches DAMP at 80 C on an
actuator, and warns below 20 % battery. A rule of your own reads `robot.get_state()` before
it sends: [guard.py](../examples/guard.py) is an example, with limits you edit. No other
example uses it by default: the motion examples carry two commented guard lines to uncomment.

```python
s = robot.get_state()  # one sample; every fact from it
if s.faulted or any(j.temp is not None and j.temp >= 80 for j in s.joints):
    raise SystemExit(f"not driving: robot mode {s.mode.name}, faulted {s.faulted}")
```

The one refusal is no live state. `stand()`, `balance()` outside MOVE, `set_velocity()`,
`trajectory()` and `set_joints()` read one cached sample before they send; on a live stream
that adds no delay, so a 50 Hz `trajectory()` loop keeps its rate. Without one (`no_state`,
`stale_state`, or a STAND sent moments ago whose report has not arrived) they wait up to the
command's `timeout`, then raise `NotReadyError` with nothing sent. `not_connected` raises at
once, and not `NotReadyError`: a closed Robot raises `NotConnectedError`, a lost link
`LinkLostError`, a robot on another protocol version `ProtocolMismatchError`. `balance()` in MOVE and `damp()` are sent at once.

`robot.preflight(action)` lists the facts. It returns a `Preflight`: `ok` when nothing is
blocking (there is live state), `problems` (blocking first, then information), `has(code)`, a
readable `str()` and `explain()` (the message a command's `NotReadyError` carries). It sends
nothing, never waits and never raises for a robot condition. The non-blocking codes are
information: nothing in the SDK acts on them. `action` is `"stand"`, `"move"` (`balance` and
`set_velocity`) or `"trajectory"` (`trajectory` and `set_joints`). `str()` reads "ready to
stand", "not ready to move:" or "not ready to run a trajectory:", then one line per problem.

| code | blocking | when |
|---|---|---|
| `not_connected` | yes | the Robot is closed, its link was lost, or the protocol version differs |
| `no_state` | yes | the session is open and the robot has not reported state |
| `stale_state` | yes | the latest state is older than 0.5 s |
| `faulted` | no | the firmware latched DAMP (or reports robot mode FAULT_DAMP), with the alerts that caused it; it stays so until the firmware restarts |
| `alerts` | no | the firmware reports active alerts, named |
| `not_armed` | no | `move` from STAND: STAND has not been held upright for 0.5 s |

**Armed.** The firmware reports STAND at once but enters MOVE only once STAND has been held
upright (gravity z below -0.87) for 0.5 s. The firmware does not refuse or report a velocity
that arrives before that: it keeps the robot in STAND. `stand()` returns only once the robot
is armed, so `stand()` then `balance()` enters MOVE. After `stand(wait=False)`, `balance()`
sends as soon as STAND is reported, and times out saying the robot was not armed; the SDK
counts the 0.5 s from its own samples, so a new session sees an armed robot as armed 0.5 s
after it connects. See [check.py](../examples/check.py), [stand.py](../examples/stand.py),
[balance.py](../examples/balance.py) and [walk.py](../examples/walk.py).

**Support the robot before MOVE -> STAND and before DAMP.** STAND has no balance loop, so a
free-standing robot asked to stand from MOVE tips over; DAMP drops a standing robot. Before
either, the robot hangs from its gantry hook or is seated on a stool or bench. The SDK sends
both from any robot mode; [rest.py](../examples/rest.py) asks first, then goes MOVE -> STAND
-> DAMP.

## Robot modes and what is sent

| call | robot mode before | robot mode after | sent |
|---|---|---|---|
| `stand()` | DAMP | STAND (returns once armed) | once |
| `stand()` | MOVE (support the robot first) | STAND (returns once armed) | once |
| `balance()` | STAND, armed | MOVE at zero velocity (returns once MOVE is reported) | once |
| `balance()` | MOVE | MOVE at zero velocity, any hold ended | once |
| `balance()` | DAMP | DAMP; `WaitTimeoutError` at once: "the robot is in DAMP: stand() first" | once |
| `set_velocity(..., duration=)` | MOVE, or STAND armed | MOVE, walking, then zero velocity | re-sent at 10 Hz until `duration` ends |
| `set_velocity(..., hold=False)` | MOVE, or STAND armed | MOVE, walking | once per call |
| `set_joints(...)` | MOVE or STAND, armed | joints under position control | `trajectory` setpoints at `hz` (50 Hz), then re-sent at 10 Hz to hold |
| `trajectory(...)` | MOVE or STAND, armed | joints under position control | once per call |
| `damp()` | any | DAMP (returns once DAMP is reported) | once |

Only a velocity and a trajectory are re-sent; `stand()`, `balance()` and `damp()` are sent
once. Every call is sent in any robot mode and the firmware decides: a velocity or
trajectory that reaches the robot in DAMP is dropped by Asimov Edge, and one in STAND before
the robot arms leaves it in STAND.

## Waiting for the robot

`robot.wait_until(predicate, timeout=10.0, stale_after=None)` blocks until a state sample
satisfies `predicate` and returns that sample. It reads the robot's own report, so it waits
on what happened, not on a timer. It raises `WaitTimeoutError` past `timeout`,
`StateStaleError` when the stream goes quiet, and `RobotFaultedError` when the firmware
latches DAMP, checked before the predicate so a fall is never read as success.
[wait_until.py](../examples/wait_until.py) starts an elbow move with `set_joints(wait=False)`
and acts the moment the robot reports the elbow past an angle, while the move is still running:

```python
robot.set_joints(target, duration=3.0, wait=False)  # returns at once; the move runs
passed = robot.wait_until(lambda s: s.joint("L_Elbow").pos >= 0.3, timeout=5.0)
print(passed.joint("L_Elbow").pos)  # the sample that satisfied the predicate
```

## Streaming from your own loop

`set_velocity(hold=False)` and `trajectory()` each send one packet per call, at the rate your
loop calls them, and re-send nothing. Each call checks for live state once and adds no delay
on a live stream; on a stale one, the call raises `NotReadyError` at once, so a loop fails
fast instead of stalling. `set_velocity(hold=False)` also ends any hold that was running, so an
older velocity is never re-sent, and still applies the limits (`Sent.clamped`). It takes no
`duration` and no `wait=True`: either is a `ValueError`.

```python
period = 1.0 / 50  # 50 Hz
while walking:
    robot.set_velocity(vx=next_vx(), hold=False)  # one packet
    time.sleep(period)
robot.balance()  # zero velocity, balancing in place
```

If your loop stops, nothing re-sends its last command. On udp and hybrid, Asimov Edge zeroes
velocity 2 s after the last packet and the robot keeps balancing in MOVE; on livekit, Asimov
Edge stops a held velocity when the SDK sends zero or leaves the room. Asimov Edge puts the
robot in DAMP 2 s after the last trajectory setpoint. `balance()`, `close()` and the exit
hook still send zero after a velocity packet.

`set_joints()` is the managed form of `trajectory()`: it moves every joint smoothly
(minimum-jerk) from the current pose to the target over `duration`, streaming `trajectory`
setpoints at `hz` (50 Hz by default) from a background thread, returns once the joints are
within `tolerance` (`wait=True`), and then holds the target by re-sending it until another
command. `trajectory()` is the protocol's raw command: one setpoint per call, for a loop you
clock yourself. See [stream_velocity.py](../examples/stream_velocity.py) and
[move_joints.py](../examples/move_joints.py).

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
- **The SDK sends zero** on `balance()`, and on `close()`, the end of a `with` block, a lost
  link or interpreter exit (for a script that never called `close()`) while it holds a
  velocity.
  When the robot latches a fault or its firmware restarts, the SDK releases the hold and
  sends nothing more. `duration=` bounds a hold on the client; the SDK sends the zero itself
  when time is up. By default `set_velocity` blocks until then (`wait=True`, which needs a
  `duration`); `wait=False` returns at once, and the next verb, or `close()` at the end of a
  `with` block, cuts an unexpired hold short.
- `close()` sends a zero if a velocity was held and stops re-sending a held trajectory.
  `LinkLostError` (no state for `link_timeout` seconds) does the same, then every verb raises
  until you `close()` and `connect()` again. Asimov Edge puts the robot in DAMP 2 s after
  the last trajectory setpoint; there is no neutral setpoint the SDK could send instead.
- A verb is never dropped as superseded; only the keepalive's re-sends are. A new verb ends
  any running `set_joints()`.
- **MOVE from STAND only once armed** (see Guards are yours). A velocity (`balance()` or
  `set_velocity()`) that reaches an armed robot in STAND puts it in MOVE. In DAMP the SDK
  sends `balance()`, `set_velocity()`, `trajectory()` and `set_joints()` as asked, and Asimov
  Edge drops a velocity or trajectory that reaches it there; `balance()` then says "the robot
  is in DAMP: stand() first".
- `trajectory()` and `set_joints()` put every joint under position control with the walking
  policy off: the robot does not balance itself while one is in force. On a standing biped
  use them with the robot supported, or with gains known to hold the legs. Without `kp`/`kd`,
  Asimov Edge applies its own per-joint gain table. Positions are in the frame
  `state.joint_pos` reports. On the biped each ankle is reported as its two motors (A, B),
  and the SDK sends those as the ankle pitch and roll the firmware reads there. The firmware
  limits ankle pitch to 0.35 rad and roll to 0.1 rad, so a trajectory of the reported pose
  holds the robot still when both ankles are within those limits, as they are standing.
  `trajectory()` and `set_joints()` raise `ValueError` for an ankle target more than 0.02 rad past
  a limit, before anything is sent. `set_joints()` holds its target until another
  verb; `trajectory()` is one setpoint you clock yourself. Joint control ends in DAMP, with
  the robot still supported: call `damp()`, or stop sending and Asimov Edge puts the robot in
  DAMP 2 s after the last setpoint, as [move_joints.py](../examples/move_joints.py) does.
- **`stand()` holds a pose without a balance loop.** It blends every joint to a fixed pose and
  holds it with position gains, with no balance loop. It brings the robot out of DAMP
  (DAMP, then STAND; `balance()` then puts it in MOVE), and it returns once the robot is
  armed. Asking a free-standing biped to stiffen after walking tips it over; the SDK sends
  `stand()` from MOVE as asked, so hang the robot from its gantry hook or seat it on a stool
  or bench first. To stand still after a walk, call `balance()` and stay in MOVE at zero
  velocity, where the policy keeps balancing.
- **The last command in effect wins; `set_joints()` yields to any other verb.** The firmware obeys
  whichever command arrived last. A `set_joints()` is fenced by the SDK: any verb from any
  thread (`damp()`, `stand()`, a velocity) ends it before its next setpoint leaves.
  A loop you clock yourself with `trajectory()` is not: a verb sent from another thread
  can be overwritten by your next setpoint. Stop your loop, then send the verb.
- `damp()` puts the robot in DAMP: every actuator stops holding its position, so a standing
  robot falls. The SDK sends it only when you call it, whatever the robot's state, and
  returns once the robot reports DAMP.
- **Faults latch.** A critical alert (a fall, actuator over-temperature, battery protection,
  a joint past its limit, lost actuator communication, a watchdog) makes the firmware latch
  DAMP until it restarts. `error_flags` stay set and `state.faulted` stays True until then.
  A firmware that reports robot mode FAULT_DAMP (`Mode.FAULT_DAMP`) is treated the same way.
  `preflight()` reports `faulted` as information. Commands are still sent; the waits of
  `stand()`, `balance()`, `set_velocity(wait=True)`, `set_joints(wait=True)` and
  `wait_until()` raise `RobotFaultedError`; `damp()` returns, since the robot is already in
  DAMP. Nothing a script sends clears it.
- **Outcomes are Unknown.** Asimov Edge reports no per-command verdict, so
  `sent.wait_outcome()` is `Unknown` on every connection mode and `on_refused` never fires.
  Read the effect from `robot.get_state()`.
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

Every robot or link error subclasses `MenloError`:

```text
MenloError
├── ConnectError
│   └── ProtocolMismatchError
├── NotConnectedError
├── LinkLostError
├── UnsupportedError
├── NotReadyError            no live state to send against; nothing was sent
│   └── RobotFaultedError    a wait saw the firmware latch DAMP (.sent: what was sent)
├── WaitTimeoutError         sent, but the robot did not get there in time (also TimeoutError)
│   └── StateStaleError      the state stream went quiet during a wait
├── CommandRefusedError
└── OutcomeUnknownError
```

| Exception | When | Carries |
|---|---|---|
| `ConnectError` / `ProtocolMismatchError` | a missing field for the mode; no state within `timeout`; protocol version differs | |
| `NotConnectedError` | a call before `connect()` or after `close()`; `get_state()` (and `damp()`) in a `require_state=False` session before the robot reported | |
| `LinkLostError` | no state for `link_timeout` s; the session is over | |
| `NotReadyError` | `stand()`, `balance()`, `set_velocity()`, `trajectory()` or `set_joints()` had no live state within `timeout` (`no_state`, `stale_state`; a `require_state=False` session waits for its first sample); nothing was sent | `.action`, `.preflight`, `.problems`, `.has(code)` |
| `RobotFaultedError` | a wait saw a latched fault, after the command was sent | `.state`, `.sent`, and all of `NotReadyError` (`.problems` holds `faulted`) |
| `WaitTimeoutError` | `stand()` did not arm, `balance()` did not reach MOVE (at once from DAMP), `damp()` did not reach DAMP, `set_joints(wait=True)` did not reach the target, or `wait_until` timed out | `.sent`, `.last` (the last state) |
| `StateStaleError` | the stream went quiet during a wait | as `WaitTimeoutError` |
| `CommandRefusedError` / `OutcomeUnknownError` | from `Sent.require()` | |
| `UnsupportedError` | this robot, over this connection, does not provide the capability | |

`RobotFaultedError` is a `NotReadyError`; catch it first to tell a latched fault apart.
Every message names what the robot reports and what to do:

```text
not ready to move: the latest state is 1.2 s old (limit 0.5 s) (stale_state); check the network link to the robot (waited 5.0 s)
the firmware latched DAMP (FALL_DETECTED); it stays latched until the firmware restarts, and nothing the SDK sends clears it
sent zero velocity, but the robot is in DAMP: stand() first
sent zero velocity, but the robot was not in MOVE within 5.0 s: the robot reports STAND and was not armed when the zero velocity was sent: ... Wait until robot.armed is True, then balance() again
```

```python
from menlo.asimov import NotReadyError, RobotFaultedError, WaitTimeoutError

try:
    robot.stand()
    robot.balance()
    robot.set_velocity(vx=0.3, duration=3.0)
except RobotFaultedError as exc:  # sent; latched until the firmware restarts
    print("faulted:", exc)
except NotReadyError as exc:  # no live state: nothing was sent
    print(exc)
except WaitTimeoutError as exc:  # sent, but the robot did not arm or reach MOVE in time
    print(exc)
```

Caller mistakes stay builtins: `ValueError` for a non-finite velocity, a bad limit or
duration, a trajectory of the wrong length, or an unknown `MENLO_MODE`; `KeyError` for an
unknown joint name; `ValueError` too for `set_velocity()` without a `duration` while it waits,
or with `duration` or `wait=True` and `hold=False`.

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
They run every example this way. The interactive one (`keyboard.py`) gets a scripted key
source, and the `livekit_raw/` scripts that only read a room are skipped: they
need a LiveKit server. CI runs the same checks on Python 3.12, 3.13 and 3.14 and builds the wheel.

```
src/menlo/
  __init__.py       __version__; one subpackage per robot
  cli/              the `menlo` console script: setup, robots, status, stand, balance, walk, damp
src/menlo/asimov/   the Asimov biped
  robot.py          Robot: verbs, the live-state check, waits, preflight, keepalive, callbacks, set_joints
  _preflight.py     Preflight, Problem, the problem codes
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
examples/           runnable scripts and livekit_raw/; indexed in examples/README.md
skills/menlo-sdk/   the usage guide as an agent skill (SKILL.md), and .claude-plugin/ to install it
```
