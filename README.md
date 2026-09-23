# menlo-sdk

`pip install menlo-sdk` · `from menlo.asimov import Robot` · repo [menloresearch/menlo-sdk](https://github.com/menloresearch/menlo-sdk)

Drive an Asimov robot from Python.

```bash
menlo login http://asimov.local --credential <from `asimovctl sdk-token create` on the robot>
```

```python
from menlo.asimov import Mode, Robot

with Robot().connect() as robot:  # the robot you logged in to; returns once it has reported state
    if robot.state.mode is Mode.DAMP:  # STAND is the wake-up verb; never stiffen a balancing robot
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=15)
    robot.set_velocity(vx=0.25, duration=4.0, wait=True)  # m/s, held 4 s, zero sent, then returns
    robot.wait_for(Mode.MOVE)
    # The robot is now in MOVE at zero velocity, balancing — that is how it stands still.
    # Do not ask for STAND here: it is a stiffen with no balance loop — see below.
    print(robot.state.joint("L_Knee").pos, robot.state.battery)  # battery is None without a BMS
    print(robot.camera.photo().to_jpeg()[:4])  # camera, microphone and speaker ride the same room
```

`Robot()` with no config finds the robot: `MENLO_MANAGER_URL` + `MENLO_CREDENTIAL` in the
environment, else the default in `~/.menlo/robots.toml` (written by `menlo login`, or by
`connect(persist=True)` / `MENLO_PERSIST=1` after a connect that worked), else a
`ConnectError` naming both. `menlo robots`, `menlo use <name>` and `menlo logout <name>`
manage the store; `$MENLO_HOME` moves it; the file is 0600. Writing agents: start from
[`docs/SKILL.md`](docs/SKILL.md).

Or say where the robot is, lane by lane:

```python
from menlo.asimov import ConnectionConfig, ManagerConfig, Robot, UdpConfig

cfg = ConnectionConfig(
    udp=UdpConfig(host="asimov.local"),  # the LAN lane
    livekit=ManagerConfig(url="http://asimov.local", credential=CRED),  # the robot's manager
)
with Robot(cfg).connect("hybrid") as robot:  # or "udp" / "livekit" — same Robot, pick per session
    ...
```

One `Robot`, one API, and the lane is chosen last. Describe the robot's wires once in a
`ConnectionConfig` — one typed class per lane, nothing mixed — bind a `Robot` to it, then
`connect(mode)`; `close()` and `connect()` again to switch lanes on the same `Robot`.
`connect()` with no mode takes the config's one lane (a manager alone is `"livekit"`).
Commands land in the edge's arbiter beside the robot's other controllers and pass the same
safety layer, whichever lane they arrive on.

## Three ways to reach a robot

| `connect(mode)` | control + state | video + audio | needs a LiveKit server | config slots |
|---|---|---|---|---|
| `"udp"` | UDP 8850 / 8851 | — | no | `udp=UdpConfig(host)` |
| `"hybrid"` | UDP 8850 / 8851 | LiveKit | yes | `udp=…` and `livekit=…` |
| `"livekit"` | LiveKit data packets / data track | LiveKit | yes | `livekit=…` |

The `livekit` slot is either `ManagerConfig(url, credential)` — the SDK asks the robot's
manager for the room and a fresh join token on every connect, so you never hold a LiveKit
token — or `LiveKitConfig(url, room, token)` when you run your own SFU.
`cfg.available_modes()` says which modes a config can reach. The manager URL is whatever
the robot's web UI answers on — `http://192.168.22.32` (port 80), `http://asimov.local:8080`,
or a bare host (`http://` assumed); no port is assumed. A manager whose SFU runs beside the
edge reports `ws://localhost:7880`; the SDK substitutes the manager's host so the room is
reachable from your machine.

Same verbs, same waits, same error model on all three: `robot.py` does not know which wire
it is on. Pick **udp** on the LAN when you need no camera, **hybrid** on the LAN when you
do, and **livekit** when the robot is not routable from your machine.

**LiveKit is optional.** `pip install menlo-sdk` with no extra drives a robot over UDP;
every `livekit` import in the SDK is lazy and confined to one module. Add the media lane
with `pip install "menlo-sdk[livekit]"`.

### The wire

```
udp / hybrid      commands -> udp/8850            one bare asimov.io.RobotCommand per datagram
                  state    <- udp/8851            one bare asimov.io.RobotState per datagram
livekit           commands -> data topic "commands"   the SAME RobotCommand bytes, reliable packets
                  state    <- data track "state"      the SAME RobotState bytes, one per frame,
                                                      ordered; user_timestamp = edge receive clock
                  camera   <- a video track, decoded to rgb8 Frames
                  mic      <- an audio track, as pcm_s16le AudioChunks
                  speaker  -> an audio track the SDK publishes
```

No envelope, no framing, no type tag: the port, or the topic, says what the bytes are.

**The SDK never holds a LiveKit API secret.** There is no `api_key`/`api_secret` parameter
anywhere in it — a caller brings a join token minted by the robot's manager, or a callable
that mints a fresh one per join (`token=lambda: fetch()`).

**And no `identity` parameter.** A participant's identity is a claim inside the token
(`sub`), and the LiveKit server ignores whatever a client says about it — an argument for
it would be a lie. The SDK reads it back instead: `transport.identity`, and
`robot.info.endpoint` reads `room@url as <identity>` once joined. One token is one
participant: two participants in a room need two tokens, or the server disconnects the
earlier duplicate. With `ManagerConfig` every session gets its own identity,
`sdk-<credential id>-<host>-<6 random hex>`, so two scripts on one credential coexist;
`ManagerConfig(label="agent")` fixes the suffix when you want a recognisable name — and two
sessions with the same label then evict each other.

## Install

Python 3.12 or newer.

```bash
uv add menlo-sdk                    # from PyPI; the robot is `menlo.asimov`
uv add "menlo-sdk[livekit]"         # + the media lane (hybrid and livekit modes)

# an unreleased commit, straight from git:
uv add "menlo-sdk @ git+https://github.com/menloresearch/menlo-sdk.git"
```

Releases are the `v*` tags of this repo, published to PyPI by `.github/workflows/publish.yml`;
how versions are chosen and cut is in [RELEASING.md](RELEASING.md).

Two runtime dependencies, `asimov-protocol` (the generated `asimov.io` bindings, from PyPI;
`>=1.2.1rc1,<2`, the bound following the wire directory `v1/`) and `protobuf`; `[livekit]` is the
one extra — a robot drives without it. The edge, its tools and this SDK import the same
installed `asimov_protocol`, so one process holds one set of descriptors.

## The robot side

For **udp** and **hybrid**, the edge must run with `--udp-control` and push state to
your machine (`--udp-state-host <your ip>`). To run against a simulated robot instead of
hardware: `menlo-studio up --container --sdk`, then `Robot(ConnectionConfig(udp=UdpConfig("127.0.0.1"))).connect("udp")`.

For **hybrid** and **livekit**, the robot's edge joins a LiveKit room — one per robot,
named by its id — publishes its camera and microphone as ordinary tracks, and (in livekit
mode) answers on the `state` data track. With `ManagerConfig` the SDK gets the room name
and a token from the robot's manager itself. `examples/agent_room.py` documents the
room/identity/topic convention and shows a
LiveKit *agent* joining the same room: `livekit-plugins-google`'s
`RealtimeModel(video_input=True)` already turns the robot's video track into what Gemini
Live wants (about 1 fps of JPEG plus 16 kHz PCM), so this SDK adds no model glue.

## API in one screen

```python
robot = Robot()  # MENLO_MANAGER_URL + MENLO_CREDENTIAL, else ~/.menlo/robots.toml
cfg = ConnectionConfig(
    udp=UdpConfig(host, command_port=8850, state_bind=("0.0.0.0", 8851), state_source=None),
    livekit=ManagerConfig(url="http://host", credential=CRED, label=None),
    # or: livekit=LiveKitConfig(url="ws://host:7880", room="robot-<serial>", token=str_or_callable)
)
cfg.available_modes()  # ("udp", "hybrid", "livekit")
robot = Robot(cfg, limits=None, link_timeout=2.0)  # bound, no network yet
robot.connect("hybrid", timeout=5.0, media_timeout=3.0, connect_timeout=10.0)  # returns robot
robot.connect()  # the config's one lane; ValueError when it has several
robot.connect(
    "livekit", require_state=False
)  # media now; state/verbs unlock when the firmware reports
robot.connect("livekit", persist=True)  # save URL + credential to the store after success
robot.close()
robot.connect("udp")  # switch lanes on the same Robot
robot = Robot(transport, limits=None, link_timeout=2.0)
robot.open()  # any Transport

# verbs — each returns a Sent immediately; the wire's own vocabulary
robot.set_velocity(
    vx, vy, vyaw, duration=None, wait=False
)  # held at 10 Hz until superseded/stop/duration
robot.set_velocity(
    vx=0.25, duration=1.0, wait=True
)  # blocks until the hold ended and its zero left
robot.stop()  # zero velocity; stays in MOVE at rest, still balancing — how it stands still
robot.stand()  # one-shot; STIFFEN to a pose, no balance loop — see the warning below
robot.damp()  # one-shot; motors compliant NOW — the emergency verb
robot.trajectory(
    positions, kp=None, kd=None
)  # one setpoint, radians, firmware order; kp/kd <= 0 -> firmware DAMP gains
robot.goto(positions, duration=2.0, hz=50, wait=True)  # clocked, interpolated from the current pose

# waits — the robot's own report, never a sleep
robot.wait_for(Mode.STAND, timeout=10, stale_after=None)
robot.wait_until(lambda s: s.upright and s.mode is Mode.MOVE, timeout=10, stale_after=None)

# state
s = robot.state  # latest sample: mode, joints, gravity, gyro, quat, euler, alerts, battery
s.age_s
s.upright
s.faulted
s.yaw  # heading, rad, counter-clockwise; a turn is math.remainder(after - before, math.tau)
s.joint("L_Knee").pos
s.battery.soc_percent
robot.info  # transport, endpoint, dof, joint_names, protocol_version, limits, capabilities
robot.has("camera")
robot.require("drive", "battery")

# media — UnsupportedError when this robot/transport does not carry it
robot.camera.photo(timeout=5)  # ONE fresh rgb8 Frame; WaitTimeoutError if the camera is quiet
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
  `set_velocity` returns at once either way — the next verb, or `close()` at the end of a
  `with` block, cuts an unexpired hold short. `wait=True` blocks until the hold has ended
  and its zero has gone out.
- `close()` sends a zero if a velocity was held and stops re-sending a held trajectory.
  `LinkLostError` (no state for `link_timeout` seconds) does the same, then every verb raises
  until you `close()` and `connect()` again. A trajectory that is no longer re-sent is DAMPed by
  the edge two seconds later; there is no neutral setpoint the SDK could send instead.
- A verb is never dropped as superseded; only the keepalive's re-sends are. A new verb ends
  any running `goto()`.
- `trajectory()` and `goto()` put every joint under position control with the walking
  policy off: the robot does not balance itself while one is in force. On a standing biped
  use them with the robot supported, or with gains known to hold the legs. The edge DAMPs a
  trajectory two seconds after the last setpoint; `goto()` holds its target until another
  verb, `trajectory()` is one setpoint you clock yourself.
- **`stand()` is a stiffen, not a balance.** It blends every joint to a fixed pose and
  holds it with position gains, with no balance loop. It is the wake-up verb
  (DAMP → STAND → MOVE) and is safe on a robot that is held, craned or on its stand.
  Asking a free-standing biped to stiffen after walking tips it over. To stand still
  after a walk, stay in MOVE at zero velocity, where the policy keeps balancing.
- **Your own setpoint loop owns the robot; `goto()` does not.** The firmware obeys
  whichever command arrived last. A `goto()` is fenced by the SDK: any verb from any
  thread — `damp()`, `stand()`, a velocity — ends it before its next setpoint leaves.
  A loop you clock yourself with `trajectory()` is not: a mode verb sent from another
  thread is overwritten by your next setpoint. Measured: a `damp()` fired into a
  hand-rolled 50 Hz trajectory loop left the robot in MOVE and upright, as if never
  sent. Stop your loop, then send the verb. In an emergency kill the process — the edge
  DAMPs by itself about two seconds after the last setpoint, and that does not depend
  on your loop still working.
- `damp()` folds a standing biped. It is deliberate and never implied by anything else.
- Speeds are clamped client-side (`Limits`, default 0.6 m/s / 1.5 rad/s), the clamp is
  visible on `Sent.clamped`, and `Limits` rejects negative or non-finite values.
- A capability is claimed from a track that ARRIVED. A LiveKit room publishing no video
  makes `robot.has("camera")` False and `robot.camera` raise `UnsupportedError`, rather
  than hand out a stream that never yields. `media_timeout=` bounds the wait and returns as
  soon as the video track is up, so a robot with a camera and no microphone does not pay
  the whole budget. A track that lands after the connect still attaches and still works; it
  simply misses `robot.info.capabilities`, which is a snapshot — raise `media_timeout` when
  a late track must be reflected there.
- The state port is plain UDP: samples that do not look like this robot are dropped, an
  older datagram never overwrites a newer sample, and `state_source=` pins the one address
  state may arrive from.
- Datagrams on the UDP lane are **unsigned**; a robot whose edge enforces signed commands
  drops them. The edge's signed-command grants are its own namespace, separate from the
  SDK's `has()`/`require()` capabilities: a `trajectory` needs `control.skills`, a
  `set_velocity` needs `control.drive`, and `stand()`/`damp()` need `control.mode` — a
  client cleared to drive is not automatically cleared to send joint targets.
- The robot's fault latch outlives the alert that raised it: after a fall the robot stays
  DAMPed and refuses STAND until its firmware restarts, while `state.faulted` clears after
  about 2.5 s. A `wait_for(Mode.STAND)` in that condition ends in `WaitTimeoutError`.

## Errors

| Exception | When |
|---|---|
| `ConnectError` / `ProtocolMismatchError` | no state within `timeout`; protocol version differs |
| `NotConnectedError` | a call before `connect()` or after `close()`; `state`/verbs in a `require_state=False` session before the robot reported |
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
make live          # a robot or simulator; MENLO_SDK_LIVE_HOST=<host>
make livekit       # real livekit.rtc vs `livekit-server --dev`; needs MENLO_SDK_LIVEKIT_URL
                   # plus TWO tokens for one room (_TOKEN and _EDGE_TOKEN)
```

CI runs the checks on Python 3.12 and 3.13 and builds the wheel. One job needs read access
to another menloresearch repository and skips with a warning when the repository secret is
absent: the real-edge integration job.

```
src/menlo/
  __init__.py       __version__; one subpackage per robot
  cli.py            the `menlo` console script: login / robots / use / logout
src/menlo/asimov/   the Asimov biped
  robot.py          Robot: verbs, waits, keepalive, callbacks, goto
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
                    _wire.py (the protobufs both lanes share) and _livekit_client.py
                    (the ONE module that imports livekit, lazily)
examples/           runnable scripts; examples/demos/ are the three walkthroughs,
                    examples/agent_room.py is the LiveKit-agent room convention
docs/SKILL.md       the two-page reference for an agent writing a script against this SDK
```

## License

MIT. The `asimov.io` bindings are the `asimov-protocol` package (MIT, `menloresearch/asimov-protocol`).
