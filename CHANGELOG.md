# Changelog

All notable changes to asimov-sdk. Pre-1.0: minor versions may break the API.

## Unreleased

- The generated `asimov.io` bindings are vendored into the wheel (`asimov_sdk/_vendor`,
  asimov-protocol v1.1.0 / d753b84) with `protobuf` as the only runtime dependency, so
  `pip install asimov-sdk` and CI need no access to the protocol repository. An installed
  `asimov-protocol` package is preferred when present (one descriptor set per process).
  `scripts/vendor_protocol.sh <tag>` re-vendors.

Adversarial codex pass against the peer SDKs (2026-09-07):

- `Limits` validates its values: finite and non-negative, or `ValueError`. A negative limit
  used to turn `stop()` into forward motion (clamp of 0 into [-l, l] with l < 0).
- Waits raise `NotConnected` on a closed `Robot` instead of succeeding on a cached sample.
- `stop()` reports itself as `stop` on its `Sent` and in refusal messages.
- Outcomes for sequences this session never sent are dropped, not surfaced as refusals.
- `connect_direct(..., link_timeout=)`.

tokamak-pm round 1 (approved; two Important items, both fixed):

- A `Robot` reopened after `close()` waits for a fresh state sample instead of passing the
  protocol check on the previous session's last sample.
- `Refused` and `Unknown` name the verb that produced them (`stand refused: FAULT_DAMPED`),
  not the literal word "command".
- `connect_direct(..., state_source=)` / `UdpTransport(state_source=)`: optional allowlist
  for the one address state may arrive from; everyone else is dropped before decoding.
- Resolved outcomes leave the pending table immediately; `outcomes()` drains under the lock.
- A reordered (older-sequence) state datagram no longer overwrites a newer `robot.state`;
  measured 196 backwards steps in one run through a 20 %-reordering proxy before the fix.
- CI: the git credential rewrite is scoped to `github.com/menloresearch/`, not all of GitHub.
- A `Robot` reopened after `LinkLost` (close, then open) is a real reconnect: the previous
  session's `LinkLost` is cleared, so `connected` and the verbs work again.
  The previous session's pending outcomes, refusals and last mode command are dropped too.
  The robot's identity (`info`) is learned again, so a reopen against a different unit works.

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
