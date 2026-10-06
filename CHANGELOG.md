# Changelog

All notable changes to menlo-sdk. Pre-1.0: minor versions may change the API.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.0rc9 — 2026-10-06

### Added
- The usage guide is an agent skill, `skills/menlo-sdk/SKILL.md`, in the Agent Skills format.
  Claude Code installs it from this repository's marketplace
  (`/plugin marketplace add menloresearch/menlo-sdk`, then `/plugin install menlo-sdk@menlo`);
  Codex, pi and other agents that read Agent Skills install it with
  `npx skills add menloresearch/menlo-sdk`. It links the documentation and
  https://docs.menlo.ai/llms.txt.
- Examples `guard.py` (an optional guard on the user's side: joint temperature, battery, a
  latched fault and active alerts, with limits at the top of the file; it prints what it
  finds and exits non-zero when the rule fails, and the motion examples call it) and
  `rest.py` (MOVE, then STAND, then DAMP, after asking whether the robot is on its gantry
  hook or seated on a stool or bench).
- `preflight()` reports active firmware alerts by name (`alerts`), and the `menlo stand`,
  `balance`, `walk` and `damp` plans show the robot's facts: robot mode, armed, faults,
  active alerts, the hottest joint and the battery. `menlo status --json` has `alerts`.

### Changed
- Breaking: guards move to the user side. Safety is the firmware's job and command handling
  is Asimov Edge's; the SDK reports facts and no longer refuses a command because of what the
  robot reports. `stand()`, `balance()`, `set_velocity()` (including `hold=False`),
  `trajectory()` and `set_joints()` are sent in any robot mode and the firmware decides:
  `stand()` from MOVE sends STAND (support the robot first), `set_velocity()` in STAND is
  sent and an armed robot enters MOVE, `balance()` sends zero velocity in any mode. The only
  refusal left is no live state: `no_state` and `stale_state` past the command's `timeout`,
  raising `NotReadyError` with nothing sent. A closed Robot raises `NotConnectedError`, a
  lost link `LinkLostError` and a protocol mismatch `ProtocolMismatchError`, at once. In a
  `require_state=False` session these verbs wait for the first sample up to their
  `timeout`, where they raised `NotConnectedError` at once.
- Breaking: a latched fault no longer refuses a command before it is sent. The command is
  sent, and the wait that sees the fault raises `RobotFaultedError` with `.sent` set.
- Breaking: `balance()` sends as soon as there is live state; it no longer waits for the
  robot to arm. From DAMP it raises `WaitTimeoutError` at once ("the robot is in DAMP:
  stand() first"); from a STAND that was not armed it times out saying so.
- Breaking: `preflight()` is a report. Only `not_connected`, `no_state` and `stale_state` are
  blocking; `faulted`, `alerts` and `not_armed` are information, and `Preflight.ok` means
  live state.
- `menlo stand`, `balance`, `walk` and `damp` ask `Proceed? [y/N]` (unless `--yes`) after
  showing the facts, and print `Not feasible:` only without live state. `menlo stand` from
  MOVE warns to hang the robot from its gantry hook or seat it on a stool or bench first.
  Exit 3 covers no live state and a command that was sent and did not get there.
- `menlo stand` on a robot already in STAND that has not armed sends nothing, waits up to
  10 s for it to arm, and exits 3 when it does not; it printed "nothing sent" and exited 0.
- Migration: catch fewer `NotReadyError`s (only no live state, and `RobotFaultedError` from
  a wait); a script that relied on the SDK to refuse a hot, faulted or flat robot, or a
  wrong robot mode, writes that rule itself from `robot.get_state()`; `examples/guard.py`
  is a starting point.
- The skill moved from `docs/SKILL.md` to `skills/menlo-sdk/SKILL.md`, and its links to the
  examples point at GitHub so they work wherever the skill is installed.

### Removed
- The SDK's state-based checks and their codes: `wrong_mode`, `joint_hot`, `battery_low`,
  `battery_protecting`, `unknown_battery`, `unknown_joint_temp` and `unknown_gravity`, the
  refusal of a latched fault before sending, and the wait for arming before a velocity.
- `JOINT_HOT_C` and `BATTERY_LOW_PERCENT` from `menlo.asimov._preflight`: the SDK holds no
  thresholds.

## 0.1.0rc8 — 2026-10-01

### Added
- `balance(timeout=5.0, wait=True)`: MOVE at zero velocity, where the walking policy balances
  the robot in place. From an armed STAND it enters MOVE and returns once the robot reports
  MOVE, waiting for arming up to `timeout`; in MOVE it ends any velocity hold and sends zero,
  never checked, from any thread; in DAMP it raises `NotReadyError` (`stand()` it first) or
  `RobotFaultedError`, and sends nothing. It is the only way from STAND into MOVE.
- `set_velocity(hold=False)`: one velocity packet per call, nothing re-sent, for a loop that
  clocks its own commands. It ends any running hold, is checked once without waiting, and
  takes no `duration` and no `wait=True` (`ValueError`).
- Open robots are closed at interpreter exit, so a script that ends without `close()` still
  sends zero velocity after a held velocity.
- `menlo balance`: from STAND it asks, then enters MOVE; in MOVE it sends zero velocity at
  once.
- Examples `balance.py` (between `stand.py` and `walk.py`), `wait_until.py` (act the moment
  a joint passes an angle, mid-move), `stream_velocity.py` (a 50 Hz velocity loop),
  `record_audio.py` (the microphone to a WAV file) and `play_audio.py` (a WAV file on the
  speaker).
- `Mode.FAULT_DAMP`: robot mode 5, the firmware's latched emergency damping. `state.faulted`
  is true in it, `preflight()` reports `faulted`, a held velocity is released, waits raise
  `RobotFaultedError`, and `robot.armed` is `False`.
- `Preflight.explain()`: the blocking problems in one sentence each, with what fixes them.
- `NotReadyError.action`, `.problems` and `.has(code)`; `RobotFaultedError.sent`;
  `WaitTimeoutError.sent`.

### Changed
- `goto()` is `set_joints()`, with the same parameters and behaviour. `trajectory()` is
  unchanged.
- `set_velocity()` works in MOVE only: from STAND it raises `NotReadyError` (`wrong_mode`,
  "balance() it first") and sends nothing, so nothing leaves STAND for MOVE implicitly.
- `set_velocity()` waits by default: it returns once the `duration` has ended and zero
  velocity is sent. Without a `duration` it raises `ValueError`; pass `wait=False` to keep
  the velocity until the next command. `timeout` defaults to `None` (5 s with a hold).
- `walk.py` no longer enters MOVE; run `balance.py` first. `keyboard.py`: space balances
  (from STAND it enters MOVE).
- `menlo walk` needs the robot in MOVE (`menlo balance` first) and ends balancing in place.
- `trajectory()` checks readiness once and raises at once when the robot is not ready, so a
  streaming loop fails fast instead of stalling; pass `timeout=` to wait.
- `stand()`, `set_velocity()`, `trajectory()` and `set_joints()` check the robot before they send.
  When it is ready the check adds no delay. They wait up to their `timeout` for a problem
  that clears on its own (`not_armed` right after a stand, `stale_state`, `no_state`), and
  otherwise raise `NotReadyError`, or `RobotFaultedError` for a latched fault, with nothing
  sent. `balance()` in MOVE and `damp()` are never checked.
- `stand(timeout=10.0, wait=True)` returns once the robot reports STAND and is armed, so the
  next `balance()` enters MOVE. It raises `WaitTimeoutError` when the robot does not arm in
  time and `RobotFaultedError` for a fault while standing. In MOVE it raises
  `NotReadyError` (`wrong_mode`) and sends nothing.
- `damp(timeout=5.0, wait=True)` returns once the robot reports DAMP, and raises
  `WaitTimeoutError` when it does not.
- `set_velocity()` takes `timeout=` for the check: 5 s by default with a hold, 0 (check once)
  with `hold=False`. `trajectory()` takes `timeout=` too, default 0: a streaming loop fails
  fast. `set_joints()`'s `timeout` bounds the whole call, the check and the wait for the
  target.
- `RobotFaultedError` is a subclass of `NotReadyError`, so `except NotReadyError` catches
  every "the robot cannot do that now". `NotReadyError` messages name every blocking problem
  and what fixes it.
- `set_joints()` from a stale pose raises `NotReadyError` (`stale_state`) after its timeout, where
  it raised `StateStaleError`.
- `wait_until()`'s `WaitTimeoutError` message names the timeout, the robot mode and the age
  of the last state sample.
- `menlo stand` and `menlo walk` still check before the plan; after the answer the SDK
  command checks again and a refusal prints `Not feasible:`. `menlo stand` exits `3` when the
  robot does not arm within 10 s.
- The examples call the commands and print the `NotReadyError` they raise. `check.py` shows
  `preflight()` on its own.
- `connect()` with no mode on a config that has both `udp` and `livekit` connects in
  `hybrid`. Before, it raised `ValueError`. `connect("udp")` and `connect("livekit")` still
  pick one of the two.
- The SDK is licensed under the Apache License 2.0. Releases up to 0.1.0rc7 were MIT.
- The package description is "Python SDK for Menlo robots. Supports Asimov 1." The README,
  which is also the PyPI page, is short: requirements, installation, usage, and links to the
  documentation.

### Fixed
- `balance()` that refuses outside MOVE after a nonzero velocity leaves `close()` the zero it
  owes for that velocity; the refusal itself still sends nothing.
- `damp()` called from a state callback (`on_state`, `on_alert`, `on_mode_change`) sends DAMP
  and returns at once. It used to wait on the thread that delivers state, which held up
  every sample for up to 5 s. The other verbs that wait, and `wait_until()`, raise
  `RuntimeError` there with nothing sent; `wait=False` works.
- `balance()` after a fault-DAMP ended a `set_velocity(hold=False)` stream raises
  `RobotFaultedError` and sends nothing. It used to send a zero velocity to the DAMPed robot.
- `set_velocity(wait=True)` raises `RobotFaultedError` (with `sent`) when the robot
  fault-DAMPs during the walk; it used to return as if the walk had run its course.
- The `LinkLostError` for a state stream that went quiet says a zero velocity was sent only
  when one was: with nothing held, nothing is sent.
- `menlo stand` on a robot already in STAND waits for the new session to see it armed
  before it says "armed" or "not armed"; an armed robot was reported "not armed".
- `balance()` outside MOVE runs its check whatever this session sent before. After a
  `set_velocity(wait=False)` or `hold=False`, a robot another controller put in DAMP or STAND
  got a MOVE-at-zero with no check; now the hold ends, nothing more is re-sent, and the check
  raises `NotReadyError` when the robot cannot enter MOVE.
- `set_joints()` with gains it rejects (`ValueError`: a lone `kp` or `kd`, or a wrong
  length) leaves the velocity or motion in force untouched. It used to end a held velocity
  first, so `close()` no longer sent its zero.
- A state sample repeated with the same sequence and firmware clock is dropped. Each copy
  used to count as a fresh observation: it refreshed `age_s`, the checks and the waits, and
  counted toward arming, with nothing new observed. A stream with no stamps at all is still
  followed sample by sample.
- `set_velocity(duration=..., wait=True)` at zero velocity ends early as a walk's wait
  does: another verb returns it, a fault-DAMP raises `RobotFaultedError`, a lost link
  `LinkLostError`, `close()` `NotConnectedError`. It used to sleep out the duration and
  return a `Sent` whatever happened.
- `set_velocity(duration=..., wait=True)` whose keepalive thread stalls (a recording write
  that blocks) raises `WaitTimeoutError` once its time budget is spent, after ending the
  hold and sending the zero itself, past the recording hook so a blocked write does not
  hold it up. It used to return as if the hold had ended, with the
  velocity still held and no zero sent.
- A `connect()` or `open()` while another is still opening the same `Robot` raises
  `RuntimeError`. Two racing connects both used to succeed, and `close()` left the first
  transport open and its keepalive thread running.
- A `UdpTransport` reopened from its own state callback (`close()` then `open()` in
  `on_state`) ends the old reader thread. The old reader used to keep running on the
  closed socket, retrying failed reads in a busy loop beside the new one.
- `stand(wait=True)` returns only once the robot reports STAND and is armed, as documented.
  A MOVE reported after the STAND went out (another controller's) used to count as
  standing; now the wait runs to `WaitTimeoutError`, which names the robot mode it saw.
- `close()` then `open()` on the same transport starts with no camera frame and no queued
  microphone audio. The previous session's last frame and unread audio used to come back
  as the new session's, until new media arrived.
- Leaving a LiveKit room waits, up to 2 s, for audio already handed to the speaker to play
  out. The last second of `speaker.play_pcm()` audio (LiveKit's queue) used to be cut off
  when a script closed the robot right after it, as `play_audio.py` and
  `camera_and_audio.py` do.
- A verb called from a state callback while a recording runs leaves that callback marked
  as one: a wait later in the same callback raises `RuntimeError`. The recording hook's own
  callback used to clear the mark, so the wait blocked the thread that delivers state.
- `play_audio.py` plays a one-second 440 Hz tone when there is no `hello.wav` in the
  directory it runs in, instead of failing with `FileNotFoundError`.
- `move_joints.py` and `wait_until.py` refuse, send nothing and exit 1 unless the robot
  reports STAND. They used to run in MOVE too, where the robot usually stands free and a
  trajectory turns the walking policy off, so it falls.
- `damp.py` prints the `WaitTimeoutError` message, which names the E-Stop in Asimov Manager,
  and exits 1 when DAMP is not reported in time. It used to end in a traceback.

### Removed
- `Robot.stop()`: `balance()` replaces it. `menlo stop`: `menlo balance` replaces it.
- `Robot.goto()`: renamed `set_joints()`.
- `Robot.wait_ready()`: the commands check readiness themselves; `preflight()` is the check
  without a command.
- `Robot.wait_for(mode)`: `stand()` and `damp()` wait for their robot mode; for any other,
  `robot.wait_until(lambda s: s.mode is Mode.MOVE)`.
- `require_ready()` in `examples/check.py`.
- `examples/apps/` (`follow_the_ball.py` and `agent_room.py`).

## 0.1.0rc7 — 2026-09-30

### Changed
- The package description is "Python SDK for Menlo robots: Asimov 1." The README is shorter:
  installation, a quickstart in plain Python, the examples, safety, connection modes, and the
  command line near the end.
- New examples, one purpose each, with settings as constants at the top of the file and no
  command-line arguments: `check.py`, `connect.py`, `read_state.py`, `stand.py`, `walk.py`,
  `damp.py`, `keyboard.py`, `move_joints.py`, `camera_and_audio.py` and
  `record_and_replay.py`. Every script that moves the robot runs `require_ready()` from
  `check.py` first. `keyboard.py` drives in short bounded holds that stop when you let go.
- `livekit_raw/` replaces the single raw LiveKit example: a shared `manager_token.py`, and
  `send_commands.py`, `read_state.py`, `camera.py` and `audio.py`.
- `apps/follow_the_ball.py` and `apps/agent_room.py` move nothing until you press g; space
  pauses, b damps after asking, x quits.

- `walk.py` walks at 0.3 m/s; `keyboard.py` drives at 0.3 m/s forward and sideways and turns
  at 0.6 rad/s. `move_joints.py` uses `goto()`'s own 0.05 rad tolerance.
- `Preflight` reads "not ready to run a trajectory" for the `"trajectory"` action, and so do
  the `NotReadyError` messages.
- With a latched fault, `preflight("move")` and `preflight("trajectory")` report `faulted`
  alone, without `wrong_mode` telling you to stand: standing does not clear a latch.
- `menlo damp` says the robot must be supported, and names both emergency stops: the E-Stop in
  Asimov Manager, or cutting power at the battery unit.
- A connect that hears no state says that Asimov Edge also sends none while the robot's
  firmware is not reporting.

### Fixed
- `trajectory()` and `goto()` on the biped moved both ankles when asked to hold them. The
  state reports each ankle as its two motors (A, B), but the firmware reads a trajectory's
  ankle entries as the ankle's pitch and roll. The SDK now sends the pitch and roll that put
  the motors where you asked, so a trajectory of a reported pose within the firmware's ankle
  limits holds the robot still, and `goto()` reaches its target instead of timing out.
- `trajectory()` and `goto()` raise `ValueError` for a biped ankle target more than 0.02 rad
  past the firmware's ankle limits (pitch 0.35 rad, roll 0.1 rad), before anything is sent.
  The firmware would clamp such a target, so the ankle would not go where it was sent.
- `close()` on livekit and hybrid stopped its event loop without waiting for the camera,
  microphone and state readers, so a stream still closing was left open. It now waits, up to
  2 s, for them to close their streams. Command packets already queued, such as the zero
  velocity `close()` sends, are delivered before the SDK leaves the room.

### Removed
- The numbered examples `01_connect_udp.py` to `11_raw_livekit.py`.

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
