# Changelog

All notable changes to asimov-sdk. Pre-1.0: minor versions may change the API.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.0 — unreleased

### Added
- `Robot.connect(host, ...)` over the robot's LAN lane (`UdpTransport`: `RobotCommand` →
  udp/8850, `RobotState` ← udp/8851); `Robot(transport)` for any `Transport`.
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
- Two more lanes, both first-class: `Robot.connect_hybrid(host, livekit_url=, room=, token=)`
  (UDP control + LiveKit media) and `Robot.connect_livekit(url, room, token=)` (commands and
  state as bare `RobotCommand`/`RobotState`: reliable data packets on the `commands` topic in,
  frames of a data track named `state` out (ordered; `State.edge_timestamp_us` is the frame's
  `user_timestamp`, the edge's receive clock) —
  the same protobufs the UDP lane sends, no envelope, no type tag). `HybridTransport` and
  `LiveKitTransport`; `robot.py` is unchanged but for the two constructors.
- LiveKit is an EXTRA (`pip install "asimov-sdk[livekit]"`): the core still depends on
  protobuf alone, every `livekit` import is lazy inside `transport/_livekit_client.py`, and
  `Robot.connect()` never reaches it.
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
