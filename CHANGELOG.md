# Changelog

All notable changes to asimov-sdk. Pre-1.0: minor versions may break the API.

## Unreleased

## 0.1.0 — 2026-09-04

First cut.

- `Robot.connect_direct(host)`: the direct LAN lane to the edge's `UdpConnector`
  (RobotCommand → udp/8850, RobotState ← udp/8851). Returns once state is flowing and the
  `asimov.io` protocol version matches.
- Verbs: `set_velocity(vx, vy, vyaw, duration=)`, `stop()`, `stand()`, `damp()`,
  `trajectory(positions, kp=, kd=)`. Each returns a `Sent` (sequence, encoded command,
  clamp flag, outcome handle).
- Outcomes: `Applied | Refused(reason: Refusal) | Unknown`; `Sent.wait_outcome()`,
  `Sent.require()`. The edge does not report verdicts yet, so every outcome is `Unknown`.
- State: typed `State` (mode, joints by firmware name, gravity, gyro, quat, alerts,
  `faulted`, `age_s`), `RobotInfo`, `Limits`.
- Waits: `wait_for(Mode)`, `wait_until(pred, timeout=, stale_after=)` with typed exits
  `WaitTimedOut`, `StateStale`, `RobotFaulted`, `CommandRefusedError`.
- Liveness: 10 Hz hold with generation counter and bounded `duration`; `LinkLost` after
  two seconds of silence; `close()` zeroes a held velocity.
- `Transport` protocol in `transport/base.py`; `UdpTransport` is its first implementation.
- Tests: unit (fake edge on the real wire), integration (asimov-edge's real `UdpConnector`
  in-process), live (a robot or menlo-studio rig).
