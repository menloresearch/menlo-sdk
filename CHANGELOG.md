# Changelog

All notable changes to asimov-sdk. Pre-1.0: minor versions may break the API.

## Unreleased

Review round 1 (Fable + codex, independent) — safety and concurrency fixes, each with a
regression test that fails on the previous code:

- A caller's verb can no longer be dropped as "superseded" by a concurrent verb from
  another thread (`damp()` racing a control loop's `set_velocity()`): bump-and-send now
  happens under one lock hold; the generation fence guards only the keepalive's re-sends.
- `LinkLost` sends a zero velocity (best effort) if one was held — the state stream may be
  dead while the command path still reaches the edge.
- State samples that do not look like this robot (no joints; or after connect a different
  protocol version or joint count) are dropped: an empty or foreign datagram on the state
  port no longer refreshes liveness or becomes `robot.state`.
- The pending-outcome table is read under the lock (`wait_until` could raise
  `RuntimeError: OrderedDict mutated during iteration` while the keepalive ran).
- `close()` from inside `on_link_lost` (which runs on the keepalive thread) no longer
  self-joins and leave the socket open.
- The robot's hostname is resolved once at open, not on every datagram; an unresolvable
  host is `ConnectFailed`.
- `Joint.vel` is `None` when the wire did not carry it, like `current` and `temp`.
- A refused `stand()`/`damp()` only fails waits while it is the verb in force; a later
  `set_velocity()` clears it. `RobotFaulted` also fires in `Mode.UNKNOWN`.
- A reader-thread decode error or a transport exception in the keepalive is logged and
  declared `LinkLost` instead of silently killing the thread; a retried `open()` no longer
  double-subscribes.

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
