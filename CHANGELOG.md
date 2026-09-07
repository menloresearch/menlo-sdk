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
- Callbacks `on_state`, `on_alert`, `on_mode_change`, `on_refused`, `on_link_lost`,
  `on_controller_change`.
- `robot.record(path)` JSON-lines recording and `asimov_sdk.recording.load()`.
- Liveness: 10 Hz hold with a generation fence and bounded `duration`; `LinkLostError`
  after `link_timeout` seconds of silence, with a zero velocity sent; `close()` zeroes a
  held velocity; reopen with `close()` + `open()`.
- State-stream hygiene: samples that do not match the robot are dropped, an older datagram
  never overwrites a newer sample (sequence compared modulo 2^32), optional `state_source`
  allowlist.
- Generated `asimov.io` bindings vendored at asimov-protocol v1.1.0 with `protobuf` as the
  only runtime dependency; an installed identical `asimov-protocol` is preferred.
