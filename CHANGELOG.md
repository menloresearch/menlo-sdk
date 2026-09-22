# Changelog

All notable changes to asimov-sdk. Pre-1.0: minor versions may change the API.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.0 — unreleased

### Fixed
- A velocity held by `set_velocity` is released when the robot itself ends the drive: a
  fault-DAMP (critical alert while DAMPed) drops the latch so the keepalive stops re-sending
  it, and a firmware restart (sequence counter reset) drops it and fences off a running
  `goto` / trajectory re-send. Nothing is sent in its place; a `MOVE` at zero would ask a
  DAMPed or booting robot to change mode.
- `close()` from inside a state callback on the `livekit` lane no longer stalls 5 s on the
  client's own event loop and then drops the queued zero-velocity packet and the room
  leave; the client now leaves asynchronously and stops its loop once that is done.
- `connect(require_state=False)`: state that arrives while the transport is still opening
  (a LiveKit media wait) now goes through the late handshake instead of being stored raw,
  so a protocol mismatch is raised on read rather than returning a frozen sample.
- `connect(persist=True)`: a store that cannot be written (read-only or full `$ASIMOV_HOME`,
  a name that belongs to another manager) closes the session it just opened before the
  error propagates, instead of leaving the keepalive and the transport running.
- `RobotStore.put` refuses to replace an entry with one for a different manager unless the
  caller chose the name (`asimov login --name`, `persist(name=)`): a manager answering
  another robot's room can no longer take over that robot's saved URL and credential.
- A DEL byte (U+007F) in a room or robot name no longer produces a `robots.toml` that
  `tomllib` rejects.

### Added
- Zero-config connect: `Robot()` with no config resolves one from `ASIMOV_MANAGER_URL` +
  `ASIMOV_CREDENTIAL`, else from `~/.asimov/robots.toml` (`$ASIMOV_HOME`; `ASIMOV_ROBOT`
  picks a named entry), else raises `ConnectError` naming both; `connect()` with no mode
  takes the config's one lane. `asimov_sdk.store`: `RobotStore`, `StoredRobot`; the file is
  0600 in a 0700 directory. `connect(persist=True)` / `ASIMOV_PERSIST=1` save a working URL
  and credential after a successful connect, keyed by the serial in the robot's room.
- The `asimov` console script: `asimov login <manager-url> [--credential]` validates a
  credential by minting a token exactly as `connect()` does, then saves it; `asimov robots`,
  `asimov use <name>`, `asimov logout <name>`.
- `connect(require_state=False)`: the media lane without waiting for the firmware.
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
  `ManagerConfig.host`, `asimov_sdk.connection.default_label()`,
  `ConnectionConfig.from_environment()`, `ConnectionConfig.only_mode()`;
  `LiveKitTransport.room` / `HybridTransport.room`.
- `docs/SKILL.md`: the agent-facing reference for writing a script against the SDK.
- `ConnectionConfig(udp=UdpConfig(...), livekit=LiveKitConfig(...) | ManagerConfig(...))`
  describes a robot's lanes, one typed class each; `Robot(cfg)` binds without touching the
  network; `robot.connect("udp" | "hybrid" | "livekit", timeout=, media_timeout=,
  connect_timeout=)` attaches and returns the robot; `close()` then `connect()` again switches
  lanes on the same `Robot`. `cfg.available_modes()` says what a config can reach; a mode
  the config cannot carry is a `ConnectError` naming the missing slot, before any I/O.
- `ManagerConfig(url, credential, label=)`: the SDK asks the robot's manager
  (`POST /api/livekit/token`) for the LiveKit URL, the room and a fresh join token on every
  connect, so a user or agent never holds a LiveKit token. `LiveKitConfig(url, room, token)`
  is for people running their own SFU.
- The UDP lane: `UdpTransport` (`RobotCommand` → udp/8850, `RobotState` ← udp/8851);
  `Robot(transport)` + `open()` for any `Transport`.
- Verbs `set_velocity(vx, vy, vyaw, duration=)`, `stop()`, `stand()`, `damp()`,
  `trajectory(positions, kp=, kd=)`, `goto(positions, duration=, hz=, wait=)`; each returns a
  `Sent` with the encoded command, the clamp flag and an outcome handle.
- Outcomes `Applied | Refused(reason: Refusal) | Unknown`; `Sent.wait_outcome()`,
  `Sent.require()`. The UDP lane carries no verdicts; every outcome there is `Unknown`.
- Typed `State`: mode, joints by firmware name, gravity, gyro, quaternion and euler angles,
  alerts with names and timestamps, battery (`Battery`, `BatteryProtection`), `faulted`,
  `age_s`; `RobotInfo` with `capabilities`; `robot.has()` / `robot.require()`.
- Waits `wait_for(Mode)`, `wait_until(pred, timeout=, stale_after=)` with typed exits
  `WaitTimeoutError`, `StateStaleError`, `RobotFaultedError`, `CommandRefusedError`.
- Media API `robot.camera`, `robot.microphone`, `robot.speaker` (`Frame`, `AudioChunk`)
  through the `Transport` seam; `UnsupportedError` on a transport that does not carry them.
- Two more lanes, both first-class: `"hybrid"` (UDP control + LiveKit media) and
  `"livekit"` (commands and
  state as bare `RobotCommand`/`RobotState`: reliable data packets on the `commands` topic in,
  frames of a data track named `state` out (ordered; `State.edge_timestamp_us` is the frame's
  `user_timestamp`, the edge's receive clock) —
  the same protobufs the UDP lane sends, no envelope, no type tag). `HybridTransport` and
  `LiveKitTransport` underneath; `robot.py` does not know which wire it is on.
- LiveKit is an EXTRA (`pip install "asimov-sdk[livekit]"`): the core still depends on
  protobuf alone, every `livekit` import is lazy inside `transport/_livekit_client.py`, and
  `connect("udp")` never reaches it.
- `Camera.photo(timeout=)` returns ONE fresh `Frame`; `Camera.capture_clip(seconds,
  audio=True)` returns a `Clip` with `save_wav()` (stdlib `wave`), `frames_as_numpy()`,
  `save_frames()` (Pillow) and `save_mp4()` (OpenCV) — the last three raise `ImportError`
  naming the package rather than adding a dependency. LiveKit video is converted from I420
  to `rgb8`, so `Frame.to_numpy()` works.
- `Transport.silence_hint`: the transport, not `Robot`, says what to check when a connect
  hears nothing on its wire.
- No `identity` parameter on the LiveKit lanes: a participant's identity is a claim inside
  the access token and the server ignores what a client says about it, so the SDK reads it
  back (`transport.identity`, and `endpoint` reads `room@url as <identity>`) instead of
  accepting an argument it could not honour.
- Callbacks `on_state`, `on_alert`, `on_mode_change`, `on_refused`, `on_link_lost`,
  `on_controller_change`.
- `robot.record(path)` JSON-lines recording and `asimov_sdk.recording.load()`.
- Capability honesty on the room lanes: `camera`/`microphone` are claimed only once the
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
