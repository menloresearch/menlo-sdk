# Changelog

All notable changes to menlo-sdk. Pre-1.0: minor versions may change the API.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.0rc6 — 2026-09-30

### Added
- `robot.preflight(action)` checks the latest state before a stand, a walk (`"move"`) or a
  trajectory and returns a `Preflight`: `ok`, and `Problem`s with stable codes
  (`stale_state`, `faulted`, `battery_low`, `joint_hot`, `wrong_mode`, `not_armed`, ...).
  A field the robot does not report is a warning, never a guess. It sends nothing.
- `robot.wait_ready(action, timeout=5.0)` blocks until the check passes, and raises
  `NotReadyError` (carrying the `Preflight`) on timeout or at once on a latched fault.
- `robot.armed`: whether the firmware accepts MOVE now. From STAND it does only once the
  robot has been upright for 0.5 s; a velocity sent earlier leaves the robot in STAND.
  Call `wait_ready("move")` between `stand()` and the first `set_velocity`.
- Saved robots hold the whole connection: `mode`, `udp_host`, `manager_url`, `credential`,
  `room` and an optional `[robots.NAME.limits]` table. `connect()` with no argument uses the
  saved connection mode. A mode whose fields are missing fails at `connect()`, before any
  network I/O, with the `menlo robots add` command that fixes it.
- Environment variables `MENLO_UDP_HOST`, `MENLO_MODE` and `MENLO_LIMITS`, beside
  `MENLO_MANAGER_URL`, `MENLO_CREDENTIAL`, `MENLO_ROBOT`, `MENLO_HOME` and `MENLO_PERSIST`.
- `ManagerConfig.check()` tests an SDK credential with one token request and returns a
  `ManagerGrant` (room, identity, role).
- The `menlo` command: `setup` (a wizard with live checks), `robots` (list, `add`, `remove`,
  `use`), `status` (READY, NOT READY or FAULTED; `--watch`, `--json`; sends nothing), and
  `stand`, `walk --duration`, `stop`, `damp`. `--robot` and `--mode` work with every command.
- `menlo stand`, `walk` and `damp` print a one-line plan (the robot, its connection mode and
  address, its state, and what will happen, with any clamped speed) and ask
  `Proceed? [y/N]`. `-y`/`--yes` goes ahead without asking; with no terminal and no `--yes`
  they exit 2 and send nothing. When `preflight()` refuses, `stand` and `walk` print
  `Not feasible:` with the robot mode, the reason and the fix, and exit 3 without asking;
  `walk` checks again after the answer; a fault that ends a walk early is named and exits 3.
  `stop` never asks, and repeats its zero only while the robot is still reported in MOVE.
  Exit codes: 0 done, 1 error, 2 usage, 3 not feasible, 4 cancelled, 130 interrupted.
- Examples `01_connect_udp.py` to `11_raw_livekit.py` and `apps/`, indexed in
  `examples/README.md` and run by the test suite.

### Changed
- `Limits()` defaults to the firmware's velocity caps, 0.4 m/s, 0.4 m/s and 0.8 rad/s, so
  `Sent.clamped` is true exactly when the robot would not walk at the speed asked for.
  Values above the caps are sent as asked and the firmware clamps them.
- `pip install menlo-sdk` installs `livekit`, `questionary` and `rich` and drives every
  connection mode; the `[livekit]` extra is gone. `import menlo.asimov` and
  `connect("udp")` still load none of them.
- Docstrings, `docs/REFERENCE.md` and `docs/SKILL.md` state what Asimov Edge and the
  firmware do: `Sent.wait_outcome()` is `Unknown` on every connection mode, no command
  timestamp is checked, UDP commands are not authenticated, a latched fault keeps
  `error_flags` set until the firmware restarts, and nothing in the SDK is an emergency stop.

### Removed
- `menlo login`, `menlo logout` and `menlo use`: use `menlo setup`, `menlo robots add`,
  `menlo robots remove` and `menlo robots use`.
- The old examples, `examples/demos/` and `examples/checkout.py`.

### Fixed
- `ALERT_NAMES` names alert 14, `INFERENCE_FAILURE`.

## 0.1.0rc5 — 2026-09-29

No change to the SDK itself: this release exercises the new release preparation.

### Changed
- `RELEASING.md`: the release PR is opened by a workflow that sets the version and this
  changelog section, and refuses a version that is not above the current one, already tagged or
  already on PyPI.

## 0.1.0rc4 — 2026-09-25

0.1.0rc3 was tagged but never published: the release workflow refused its own tag. rc4 is the
same SDK, released with the workflow fixed.

### Changed
- Releases are cut from Menlo's internal repository, the SDK's source, and reach this
  repository as a mirrored `v*` tag with a GitHub Release carrying the files uploaded to PyPI
  (`RELEASING.md`). This repository no longer publishes by itself.

### Fixed
- The error raised when the wire bindings cannot be imported names the module that failed
  and installs both runtime dependencies, `asimov-protocol` and `protobuf`; it used to call
  protobuf the only one.

### Added
- README links `SECURITY.md` for private vulnerability reports.

## 0.1.0rc2 — 2026-09-23

### Changed
- `asimov-protocol` is a declared dependency (`>=1.2.1rc1,<2`, from PyPI) instead of a tree
  vendored into the wheel. One installed copy of the bindings per process; the `_vendor/`
  directory, `scripts/vendor_protocol.sh`, `make vendor-protocol` / `check-vendor` and the
  vendored-bindings CI job are gone.
- The distribution is `menlo-sdk` (repository `menloresearch/menlo-sdk`), published to PyPI on
  `v*` tags. The import is `menlo`, one subpackage per robot: the Asimov biped is
  `menlo.asimov` (`from menlo.asimov import Robot, Mode`). The console script is `menlo`
  (`menlo login`), the store is `~/.menlo/robots.toml` (`$MENLO_HOME`), the environment
  variables are `MENLO_MANAGER_URL`, `MENLO_CREDENTIAL`, `MENLO_ROBOT`, `MENLO_PERSIST`, and
  the error base class is `MenloError`. Between releases `__version__` carries the next
  version with a `.dev0` suffix; see `RELEASING.md`.

### Fixed
- A velocity held by `set_velocity` is released when the robot itself ends the drive: a
  fault-DAMP (critical alert while DAMPed) drops the latch so the keepalive stops re-sending
  it, and a firmware restart (sequence counter reset) drops it and fences off a running
  `goto` / trajectory re-send. Nothing is sent in its place; a `MOVE` at zero would ask a
  DAMPed or booting robot to change mode.
- `close()` from inside a state callback in the `livekit` connection mode no longer stalls 5 s on the
  client's own event loop and then drops the queued zero-velocity packet and the room
  leave; the client now leaves asynchronously and stops its loop once that is done.
- `connect(require_state=False)`: state that arrives while the transport is still opening
  (a LiveKit media wait) now goes through the late handshake instead of being stored raw,
  so a protocol mismatch is raised on read rather than returning a frozen sample.
- `connect(persist=True)`: a store that cannot be written (read-only or full `$MENLO_HOME`,
  a name that belongs to another manager) closes the session it just opened before the
  error propagates, instead of leaving the keepalive and the transport running.
- `RobotStore.put` refuses to replace an entry with one for a different manager unless the
  caller chose the name (`menlo login --name`, `persist(name=)`): a manager answering
  another robot's room can no longer take over that robot's saved URL and credential.
- A DEL byte (U+007F) in a room or robot name no longer produces a `robots.toml` that
  `tomllib` rejects.

### Added
- Zero-config connect: `Robot()` with no config resolves one from `MENLO_MANAGER_URL` +
  `MENLO_CREDENTIAL`, else from `~/.menlo/robots.toml` (`$MENLO_HOME`; `MENLO_ROBOT`
  picks a named entry), else raises `ConnectError` naming both; `connect()` with no mode
  takes the config's one connection mode. `menlo.asimov.store`: `RobotStore`, `StoredRobot`; the file is
  0600 in a 0700 directory. `connect(persist=True)` / `MENLO_PERSIST=1` save a working URL
  and credential after a successful connect, keyed by the serial in the robot's room.
- The `menlo` console script: `menlo login <manager-url> [--credential]` validates a
  credential by minting a token exactly as `connect()` does, then saves it; `menlo robots`,
  `menlo use <name>`, `menlo logout <name>`.
- `connect(require_state=False)`: media without waiting for the firmware.
  `robot.state`, `robot.info` and the motion verbs raise `NotConnectedError` until the robot
  reports, then the handshake completes on its own; a late protocol mismatch is raised
  where it is read.
- `set_velocity(..., duration=, wait=True)` blocks until the hold has ended and its zero
  has gone out (or another verb superseded it); `close()` during a waited hold raises
  `NotConnectedError`. `close()` documents that it cuts an unexpired hold short.
- `State.yaw`; `Frame.to_jpeg(quality=85) -> bytes` (Pillow, named in the `ImportError`
  when absent; JPEG frames pass through).
- `ManagerConfig`: the URL needs neither scheme nor port (`192.168.22.32`, `http://host`,
  `http://host:8080`); a loopback LiveKit URL minted by the manager (`ws://localhost:7880`,
  the robot's own view) is rewritten to the manager's host; a session with no `label` gets
  `<host>-<6 random hex>` so two sessions on one credential never share an identity.
  `ManagerConfig.host`, `menlo.asimov.connection.default_label()`,
  `ConnectionConfig.from_environment()`, `ConnectionConfig.only_mode()`;
  `LiveKitTransport.room` / `HybridTransport.room`.
- `docs/SKILL.md`: the agent-facing reference for writing a script against the SDK.
- `ConnectionConfig(udp=UdpConfig(...), livekit=LiveKitConfig(...) | ManagerConfig(...))`
  describes a robot's connection modes, one typed class each; `Robot(cfg)` binds without touching the
  network; `robot.connect("udp" | "hybrid" | "livekit", timeout=, media_timeout=,
  connect_timeout=)` attaches and returns the robot; `close()` then `connect()` again switches
  connection modes on the same `Robot`. `cfg.available_modes()` says what a config can reach; a mode
  the config cannot carry is a `ConnectError` naming the missing slot, before any I/O.
- `ManagerConfig(url, credential, label=)`: the SDK asks the robot's manager
  (`POST /api/livekit/token`) for the LiveKit URL, the room and a fresh join token on every
  connect, so a user or agent never holds a LiveKit token. `LiveKitConfig(url, room, token)`
  is for people running their own SFU.
- The `udp` connection mode: `UdpTransport` (`RobotCommand` → udp/8850, `RobotState` ← udp/8851);
  `Robot(transport)` + `open()` for any `Transport`.
- Verbs `set_velocity(vx, vy, vyaw, duration=)`, `stop()`, `stand()`, `damp()`,
  `trajectory(positions, kp=, kd=)`, `goto(positions, duration=, hz=, wait=)`; each returns a
  `Sent` with the encoded command, the clamp flag and an outcome handle.
- Outcomes `Applied | Refused(reason: Refusal) | Unknown`; `Sent.wait_outcome()`,
  `Sent.require()`. The `udp` connection mode carries no verdicts; every outcome there is `Unknown`.
- Typed `State`: mode, joints by firmware name, gravity, gyro, quaternion and euler angles,
  alerts with names and timestamps, battery (`Battery`, `BatteryProtection`), `faulted`,
  `age_s`; `RobotInfo` with `capabilities`; `robot.has()` / `robot.require()`.
- Waits `wait_for(Mode)`, `wait_until(pred, timeout=, stale_after=)` with typed exits
  `WaitTimeoutError`, `StateStaleError`, `RobotFaultedError`, `CommandRefusedError`.
- Media API `robot.camera`, `robot.microphone`, `robot.speaker` (`Frame`, `AudioChunk`)
  through the `Transport` seam; `UnsupportedError` on a transport that does not carry them.
- Two more connection modes, both first-class: `"hybrid"` (UDP control + LiveKit media) and
  `"livekit"` (commands and
  state as bare `RobotCommand`/`RobotState`: reliable data packets on the `commands` topic in,
  frames of a data track named `state` out (ordered; `State.edge_timestamp_us` is the frame's
  `user_timestamp`, the edge's receive clock):
  the same protobufs the `udp` connection mode sends, no envelope, no type tag). `HybridTransport` and
  `LiveKitTransport` underneath; `robot.py` does not know which wire it is on.
- LiveKit is an EXTRA (`pip install "menlo-sdk[livekit]"`): the core still depends on
  protobuf alone, every `livekit` import is lazy inside `transport/_livekit_client.py`, and
  `connect("udp")` never reaches it.
- `Camera.photo(timeout=)` returns ONE fresh `Frame`; `Camera.capture_clip(seconds,
  audio=True)` returns a `Clip` with `save_wav()` (stdlib `wave`), `frames_as_numpy()`,
  `save_frames()` (Pillow) and `save_mp4()` (OpenCV); the last three raise `ImportError`
  naming the package rather than adding a dependency. LiveKit video is converted from I420
  to `rgb8`, so `Frame.to_numpy()` works.
- `Transport.silence_hint`: the transport, not `Robot`, says what to check when a connect
  hears nothing on its wire.
- No `identity` parameter in the LiveKit connection modes: a participant's identity is a claim inside
  the access token and the server ignores what a client says about it, so the SDK reads it
  back (`transport.identity`, and `endpoint` reads `room@url as <identity>`) instead of
  accepting an argument it could not honour.
- Callbacks `on_state`, `on_alert`, `on_mode_change`, `on_refused`, `on_link_lost`,
  `on_controller_change`.
- `robot.record(path)` JSON-lines recording and `menlo.asimov.recording.load()`.
- Capability honesty in the LiveKit connection modes: `camera`/`microphone` are claimed only once the
  matching track is actually subscribed, and dropped when the room goes; a room with no
  video raises `UnsupportedError` instead of yielding nothing.
- Liveness: 10 Hz hold with a generation fence and bounded `duration`; `LinkLostError`
  after `link_timeout` seconds of silence, with a zero velocity sent; `close()` zeroes a
  held velocity; reopen with `close()` + `open()`.
- State-stream hygiene: samples that do not match the robot are dropped, an older datagram
  never overwrites a newer sample (sequence compared modulo 2^32), optional `state_source`
  allowlist.
- Generated `asimov.io` bindings vendored at asimov-protocol v1.1.0 with `protobuf` as the
  only runtime dependency; an installed identical `asimov-protocol` is preferred.
