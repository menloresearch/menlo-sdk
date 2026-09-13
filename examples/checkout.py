"""Checkout: wake the robot up, walk it a little, and (only if asked) damp it — with gates.

One script, four stages. Each stage is a go/no-go gate on the robot's own report, and
the sequence always ends in a safe state no matter where it fails. The same file runs
against the simulator and the real robot; only the flags differ.

    python examples/checkout.py                   # simulator: menlo-studio up --container --sdk
    python examples/checkout.py --until stand                    # wake up only
    python examples/checkout.py --vx 0.15 --walk-s 3             # gentler walk
    python examples/checkout.py --damp                           # end in DAMP
    python examples/checkout.py 192.168.1.50 --confirm --vx 0.1 --walk-s 2 --record run.jsonl

Stages
  connect  state arrives, protocol matches, nothing faulted, battery (if reported) healthy
  stand    stand(), wait for the firmware's STAND, then hold still for --stand-s while
           checking upright / tilt / alerts / freshness
  walk     set_velocity(vx, duration=--walk-s) under a 20 Hz watchdog that stops on tilt,
           fault or stale state; then zero velocity, wait until the body is quiet, and
           hold there (MOVE at rest) for the same check
  damp     only with --damp (or --damp-on-fail after a failure): the robot folds

STAND is "stiffen": the firmware blends the joints to a fixed pose and holds them there
with no balance loop. It is the wake-up verb (DAMP -> STAND -> MOVE), not the way to
finish a walk — a free-standing biped asked to stiffen after walking tips over, on the
simulator with both firmware builds tried. Standing still after a walk is zero velocity
in MOVE, where the policy keeps balancing. --after-walk stand exists for a robot that is
held or on its stand.

Real robot: pass --confirm (Enter before every motion), a small --vx, a short --walk-s,
and --damp only when the robot is held or on its stand. Keep a hand on the hardware
E-stop: everything this script sends is a network packet. On the simulator a DAMP
latches a fault that suppresses STAND until the rig restarts, so make it the last thing.

Start from DAMP. The stand stage refuses a robot that is already in MOVE, because that
robot is balancing under the walking policy and stiffening it is the tip-over this file
warns about. A successful run ends in MOVE at rest (--after-walk rest), so a second run
needs a DAMP first — end with --damp, or damp the robot yourself, if you want to go
straight round again.

Exit code 0 = every requested stage passed; 1 = a stage failed (the reason is printed);
2 = could not connect.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import math
import socket
import sys
import time

from asimov_sdk import AsimovError, ConnectError, Mode, Robot, State, WaitTimeoutError

STALE_S = 0.5  # a sample older than this means we are flying blind: stop
#: How long the gyro must stay under --quiet-rad-s before the body counts as settled.
QUIET_HOLD_S = 0.5


class CheckFailed(Exception):
    pass


class Progress:
    """What has actually been sent to the robot, recorded after the gate, not before.

    Cleanup decisions depend on this. `commanded` is "any command left this process" —
    it decides what we tell the operator. `velocity` is narrower: only a velocity makes
    `stop()` (a zero-velocity POLICY command, i.e. mode=MOVE) an appropriate thing to
    send, because on anything else it is a controller switch nobody authorised.
    """

    def __init__(self) -> None:
        self.commanded = False
        self.velocity = False


def tilt_deg(s: State) -> float | None:
    e = s.euler
    if e is None:
        return None
    return max(abs(math.degrees(e[0])), abs(math.degrees(e[1])))


def describe(s: State) -> str:
    parts = [f"mode={s.mode.name}", f"upright={s.upright}", f"age={s.age_s * 1000:.0f}ms"]
    t = tilt_deg(s)
    if t is not None:
        parts.append(f"tilt={t:.1f}°")
    if s.alerts:
        parts.append("alerts=" + ",".join(a.name for a in s.alerts))
    if s.battery is not None:
        b = s.battery
        parts.append(f"battery={b.soc_percent:.0f}% {b.voltage_v:.1f}V")
        if b.protecting:
            parts.append(f"BMS={b.protection!s}")
    return " ".join(parts)


def is_loopback(host: str) -> bool:
    """True only when every address `host` resolves to is loopback. A name that does not
    resolve counts as a robot: the safe default is to gate."""
    literal = host.strip("[]").split("%")[0]
    try:
        return ipaddress.ip_address(literal).is_loopback  # no DNS for an address literal
    except ValueError:
        pass
    try:
        # One resolution, before anything moves; a slow resolver delays startup, never a
        # command. A name that does not resolve counts as a robot.
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addrs)


def check_sample(s: State, tilt_limit: float, label: str) -> float | None:
    """One sample's go/no-go. Returns the tilt so callers can track the worst seen.

    `gravity` and `quat` are both optional on the wire. When they are missing the
    upright and tilt tests below quietly do nothing, and the stage still prints
    "worst tilt 0.0°" — a measurement it never made. Same rule as the gyro in
    `settle()`: absence of telemetry is not evidence of good attitude.
    """
    if s.gravity is None or s.quat is None:
        raise CheckFailed(
            f"{label}: the robot is not reporting attitude "
            f"(gravity={s.gravity is not None}, quat={s.quat is not None}), so upright "
            "and tilt cannot be checked"
        )
    if s.age_s > STALE_S:
        raise CheckFailed(f"{label}: state is {s.age_s:.2f}s old — the robot stopped talking")
    if s.faulted:
        raise CheckFailed(f"{label}: fault — {[a.name for a in s.alerts]}")
    if s.upright is False:
        raise CheckFailed(f"{label}: not upright (gravity z={s.gravity[2]:.2f})")
    t = tilt_deg(s)
    if t is not None and t > tilt_limit:
        raise CheckFailed(f"{label}: tilt {t:.1f}° exceeds {tilt_limit:.0f}°")
    return t


def hold_still(
    robot: Robot, seconds: float, tilt_limit: float, label: str, expect: Mode | None = None
) -> float:
    """Watch the robot for `seconds`; fail on the first bad sample. Returns the worst tilt.

    `expect` is the mode the hold is supposed to be in. Without it a hold cannot notice
    the robot leaving that mode — someone DAMPing a supported robot mid-hold leaves the
    samples fresh, upright and unfaulted, and the stage reports PASS.
    """
    worst = 0.0
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        s = robot.state
        t = check_sample(s, tilt_limit, label)
        if expect is not None and s.mode is not expect:
            raise CheckFailed(f"{label}: mode changed to {s.mode.name} during the hold")
        if t is not None:
            worst = max(worst, t)
        time.sleep(0.05)
    return worst


def settle(robot: Robot, args: argparse.Namespace, label: str) -> float:
    """After a zero velocity, wait until the body is quiet before asking for STAND.

    STAND is a different controller from the walking policy; handing over while the body
    is still swinging is how a walk ends on the floor. Waits at least --settle-s and
    until the gyro has stayed under --quiet-rad-s for half a second."""
    t_start = time.monotonic()
    quiet_since: float | None = None
    rate = 0.0
    while True:
        s = robot.state
        check_sample(s, args.tilt_deg, label)
        if s.gyro is None:
            # Absence of telemetry is not evidence of stillness. The UDP decoder can
            # legitimately produce gyro=None, and mapping that to 0.0 let a stream with
            # no gyro satisfy the quiet gate and walk straight into STAND.
            raise CheckFailed(
                f"{label}: the robot is not reporting gyro, so the body cannot be "
                "confirmed quiet before STAND"
            )
        rate = max(abs(v) for v in s.gyro)
        now = time.monotonic()
        if rate >= args.quiet_rad_s:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        settled = quiet_since is not None and now - quiet_since >= QUIET_HOLD_S
        if now - t_start >= args.settle_s and settled:
            return rate
        if now - t_start > args.settle_timeout:
            raise CheckFailed(
                f"{label}: body still moving {args.settle_timeout:.0f}s after the stop "
                f"(gyro {rate:.2f} rad/s)"
            )
        time.sleep(0.05)


def wait_damped(robot: Robot, timeout: float = 5.0) -> State:
    """Wait until the robot reports DAMP, counting a fault-DAMP as success.

    `wait_until` tests the fault BEFORE the predicate (robot.py:527-533), so
    `wait_for(Mode.DAMP)` RAISES on a robot that is already fault-DAMPed — which is the
    most likely reason we are damping at all, since `check_sample` fails on `s.faulted`
    and the firmware has DAMPed itself by then. Used inside the cleanup's
    `suppress(AsimovError)` that made `--damp-on-fail` confirm nothing at all.
    """
    deadline = time.monotonic() + timeout
    while True:
        s = robot.state
        if s.mode is Mode.DAMP:
            return s
        if s.age_s > STALE_S:
            # A frozen sample would otherwise be re-read as "still not DAMP" until the
            # deadline; say what actually happened.
            raise CheckFailed(f"damp: state is {s.age_s:.2f}s old — the robot stopped talking")
        if time.monotonic() >= deadline:
            raise CheckFailed(f"damp: still {s.mode.name} after {timeout:.0f}s")
        time.sleep(0.02)


def gate(
    args: argparse.Namespace,
    what: str,
    robot: Robot | None = None,
    expect: Mode | None = None,
) -> None:
    """Announce the next motion, and with --confirm wait for the operator.

    `input()` can block for minutes, and `set_velocity` enforces no posture, fault or
    tilt precondition of its own, so nothing about the robot is still known when Enter
    is finally pressed. Re-read it — including the mode, since a robot DAMPed by someone
    else while it sat on its stand is fresh, unfaulted, upright and level, and would
    sail through a check that only looked at those.
    """
    print(f"\n== {what}")
    if not args.confirm:
        return
    try:
        input("   press Enter to proceed (Ctrl-C to abort) … ")
    except EOFError as exc:
        # No terminal: nohup, systemd, CI, an ssh session whose stdin closed. Treat a
        # gate that cannot be answered as a refusal, not as consent.
        raise CheckFailed("gate: stdin closed, so the operator cannot confirm") from exc
    if robot is not None:
        s = robot.state
        check_sample(s, args.tilt_deg, "gate")
        if expect is not None and s.mode is not expect:
            raise CheckFailed(
                f"gate: the robot is in {s.mode.name}, not {expect.name}, since the prompt"
            )


def stage_connect(robot: Robot, args: argparse.Namespace, progress: Progress) -> None:
    info = robot.info
    print(f"   {info}")
    s = robot.state
    print(f"   {describe(s)}")
    # A looser tilt cap than --tilt-deg: at this point we only care that the robot
    # isn't lying on its side, plus the usual fault/staleness/upright checks.
    check_sample(s, tilt_limit=90.0, label="connect")
    if s.battery is not None and s.battery.protecting:
        raise CheckFailed(f"connect: the BMS is protecting ({s.battery.protection!s})")
    if s.battery is not None and s.battery.soc_percent < args.min_soc:
        raise CheckFailed(f"connect: battery {s.battery.soc_percent:.0f}% < {args.min_soc:.0f}%")
    print("   PASS connect")


def stage_stand(robot: Robot, args: argparse.Namespace, progress: Progress) -> None:
    gate(args, "stand — stiffen to the standing pose (no balancing in this mode)", robot)
    # STAND blends every joint to a fixed pose with no balance loop. Sending it into a
    # robot that is already walking — a previous run left it in MOVE, or another
    # controller (BLE, asimov-manager) has the body — puts a moving biped under position
    # control, and it goes down. The re-stand after the walk already waits for a quiet
    # body via settle(); the wake-up stand had no equivalent.
    s = robot.state
    check_sample(s, args.tilt_deg, "stand/pre")
    if s.mode is Mode.MOVE:
        raise CheckFailed(
            "stand: the robot is already in MOVE, so it is balancing under the walking "
            "policy — STAND has no balance loop and would drop it. A previous run left "
            "it there (--after-walk rest ends in MOVE at zero velocity), or another "
            "controller has the body. Start this script from DAMP."
        )
    settle(robot, args, "stand/pre")
    progress.commanded = True
    robot.stand()
    try:
        s = robot.wait_for(Mode.STAND, timeout=args.stand_timeout, stale_after=STALE_S)
    except WaitTimeoutError as exc:
        raise CheckFailed(
            f"stand: {exc} (a robot that was DAMPed after a fall keeps refusing STAND until "
            "the firmware restarts; on the simulator, restart the rig)"
        ) from exc
    except AsimovError as exc:
        # A dead link or a stale stream is a different problem, and telling the operator
        # to restart the rig sends them to the wrong place.
        raise CheckFailed(f"stand: {exc}") from exc
    print(f"   standing: {describe(s)}")
    worst = hold_still(robot, args.stand_s, args.tilt_deg, "stand", expect=Mode.STAND)
    print(f"   PASS stand — held {args.stand_s:.0f}s, worst tilt {worst:.1f}°")


def stage_walk(robot: Robot, args: argparse.Namespace, progress: Progress) -> None:
    gate(
        args,
        f"walk — forward at {args.vx:.2f} m/s for {args.walk_s:.1f}s",
        robot,
        expect=Mode.STAND,
    )
    progress.commanded = True
    progress.velocity = True
    # The SDK's bounded hold is measured from HERE (robot.py:334-336), so the watchdog
    # window must be too. Starting it after wait_for(MOVE) returns — which may take up
    # to 5 s — let the window cover seconds of a robot that had already stopped, and
    # the reported worst tilt was then not a measurement of the walk.
    t_walk = time.monotonic()
    sent = robot.set_velocity(vx=args.vx, duration=args.walk_s)
    if sent.clamped:
        print(f"   note: the SDK clamped the request to {sent.command}")
    try:
        # Not wait_for(): that tests mode and staleness only, and a fall in the window
        # between the velocity leaving and the robot reporting MOVE would go unseen for
        # up to 5 s. Poll the full go/no-go on every sample instead.
        t_move = time.monotonic() + 5.0
        while True:
            s = robot.state
            check_sample(s, args.tilt_deg, "walk/start")
            if s.mode is Mode.MOVE:
                break
            if time.monotonic() >= t_move:
                raise CheckFailed(
                    f"walk: the robot did not enter MOVE within 5s (still {s.mode.name})"
                )
            time.sleep(0.05)
        worst = 0.0
        # Reaching MOVE is not evidence the robot moved. This transport delivers no
        # command outcomes at all, the arbiter can refuse a velocity when another
        # controller holds the body, and --after-walk rest leaves a previous run sitting
        # in MOVE already — so wait_for() can return instantly on a robot that then
        # stands there. Without this, the watchdog spins over a motionless robot,
        # settle() passes on its first quiet sample, and a hardware checkout prints
        # PASS for a robot that never took a step. There is no odometry, but joint
        # travel and body rate are both non-zero for any real gait.
        rest = [j.pos for j in robot.state.joints]
        travel = 0.0
        rate = 0.0
        t_end = t_walk + args.walk_s + 0.5
        if time.monotonic() > t_walk + args.walk_s * 0.5:
            raise CheckFailed(
                "walk: reaching MOVE took more than half the walk; the window left would "
                "not be a measurement of walking"
            )
        while time.monotonic() < t_end:  # the watchdog: the hold is bounded, we still watch
            s = robot.state
            t = check_sample(s, args.tilt_deg, "walk")
            if t is not None:
                worst = max(worst, t)
            travel = max(travel, *(abs(j.pos - r) for j, r in zip(s.joints, rest, strict=True)))
            if s.gyro is not None:
                rate = max(rate, *(abs(v) for v in s.gyro))
            time.sleep(0.05)
        if travel < args.min_travel_rad:
            raise CheckFailed(
                f"walk: the robot did not move — largest joint travel {travel:.3f} rad "
                f"< {args.min_travel_rad:.3f} (peak body rate {rate:.2f} rad/s). It "
                "reached MOVE but the velocity did not take effect."
            )
        print(f"   moved: {travel:.2f} rad peak joint travel, {rate:.2f} rad/s peak body rate")
    finally:
        robot.stop()  # zero velocity whatever happened; the robot keeps its feet
    if args.after_walk == "stand":
        rate = settle(robot, args, "walk/settle")
        print(f"   walked; worst tilt {worst:.1f}°, quiet at {rate:.2f} rad/s")
    else:
        # No handover on this path: the robot stays in MOVE under the balancing policy,
        # so a micro-correcting body is not a failure. Report the rate, do not gate on it.
        s_now = robot.state
        rate = max(abs(v) for v in s_now.gyro) if s_now.gyro is not None else float("nan")
        print(f"   walked; worst tilt {worst:.1f}°, body rate {rate:.2f} rad/s (not gated at rest)")
    if args.after_walk == "stand":
        # The one motion that used to skip its gate. This file's own header says a
        # free-standing biped asked to stiffen after walking tips over, so of every
        # command here it is the one an operator most needs to authorise.
        gate(
            args,
            "re-stand — stiffen after the walk; tips a free-standing biped",
            robot,
            expect=Mode.MOVE,
        )
        s = robot.state
        check_sample(s, args.tilt_deg, "walk/re-stand")
        if s.mode is not Mode.MOVE:
            # Without --confirm the gate does not re-read the robot; do it here so a
            # robot that left MOVE meanwhile (someone DAMPed it) is not stiffened blind.
            raise CheckFailed(f"walk/re-stand: expected MOVE, the robot is {s.mode.name}")
        print("   back to STAND")
        robot.stand()
        s = robot.wait_for(Mode.STAND, timeout=args.stand_timeout, stale_after=STALE_S)
        print(f"   {describe(s)}")
        hold_still(robot, args.stand_s, args.tilt_deg, "walk/re-stand", expect=Mode.STAND)
    else:
        print(f"   holding at zero velocity (MOVE at rest): {describe(robot.state)}")
        hold_still(robot, args.stand_s, args.tilt_deg, "walk/rest", expect=Mode.MOVE)
    print("   PASS walk")


def stage_damp(robot: Robot, args: argparse.Namespace, progress: Progress) -> None:
    gate(args, "damp — motors go compliant NOW; the robot folds", robot)
    progress.commanded = True
    robot.damp()
    s = wait_damped(robot)
    print(f"   {describe(s)}")
    print("   PASS damp")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("host", nargs="?", default="127.0.0.1")
    ap.add_argument(
        "--until", choices=["connect", "stand", "walk"], default="walk", help="last stage to run"
    )
    ap.add_argument("--vx", type=float, default=0.2, help="forward speed, m/s (default 0.2)")
    ap.add_argument("--walk-s", type=float, default=3.0, help="how long to walk (default 3)")
    ap.add_argument(
        "--stand-s",
        type=float,
        default=3.0,
        help="how long to hold still after standing (default 3)",
    )
    ap.add_argument("--stand-timeout", type=float, default=15.0)
    ap.add_argument(
        "--settle-s",
        type=float,
        default=1.5,
        help="minimum quiet time at zero velocity before STAND is requested (default 1.5)",
    )
    ap.add_argument(
        "--quiet-rad-s", type=float, default=0.3, help="gyro below this counts as quiet"
    )
    ap.add_argument("--settle-timeout", type=float, default=6.0)
    ap.add_argument(
        "--tilt-deg", type=float, default=20.0, help="abort above this roll/pitch (default 20)"
    )
    ap.add_argument(
        "--min-travel-rad",
        type=float,
        default=0.05,
        help="fail the walk unless some joint moves at least this much (default 0.05); "
        "reaching MOVE is not evidence the robot actually stepped",
    )
    ap.add_argument(
        "--min-soc", type=float, default=20.0, help="refuse below this battery %% (default 20)"
    )
    ap.add_argument(
        "--after-walk",
        choices=["rest", "stand"],
        default="rest",
        help="after the walk: stay in MOVE at zero velocity (rest) or request STAND (default rest)",
    )
    ap.add_argument(
        "--confirm",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="ask before every motion. Defaults ON for any host that is not loopback, "
        "because that is a real robot; pass --no-confirm to override",
    )
    ap.add_argument("--damp", action="store_true", help="end in DAMP after the requested stages")
    ap.add_argument(
        "--damp-on-fail",
        action="store_true",
        help="DAMP if a stage fails (default: stop, stay standing)",
    )
    ap.add_argument(
        "--record", metavar="PATH", help="write every state and command to this JSON-lines file"
    )
    args = ap.parse_args()

    # The header asks the operator to remember --confirm on hardware; the parser should
    # not rely on memory. Anything that is not loopback is a robot in a room with people.
    if args.confirm is None:
        args.confirm = not is_loopback(args.host)
        if args.confirm:
            print(f"{args.host} is not loopback — gating every motion (--no-confirm to override)")

    # argparse takes "nan" and negatives happily. --stand-s nan made hold_still's loop
    # never run and the stage report PASS; --stand-timeout nan raised ValueError from
    # deep inside wait_until AFTER stand() had been sent, and ValueError is not in the
    # (CheckFailed, AsimovError) handler below, so --damp-on-fail never ran.
    positive = (
        "vx",
        "walk_s",
        "stand_s",
        "stand_timeout",
        "settle_s",
        "quiet_rad_s",
        "settle_timeout",
        "tilt_deg",
    )
    for name in positive:
        v = getattr(args, name)
        if not math.isfinite(v) or v <= 0.0:
            ap.error(f"--{name.replace('_', '-')} must be a finite positive number, got {v!r}")
    if not math.isfinite(args.min_soc) or not 0.0 <= args.min_soc <= 100.0:
        ap.error(f"--min-soc must be a percentage between 0 and 100, got {args.min_soc!r}")
    if not math.isfinite(args.min_travel_rad) or args.min_travel_rad < 0.0:
        ap.error(f"--min-travel-rad must be finite and >= 0, got {args.min_travel_rad!r}")
    # settle() needs settle_s elapsed AND 0.5s of quiet before it can return, so a
    # timeout below that can never succeed — and it then blames a perfectly still robot
    # for "still moving".
    if args.settle_timeout <= args.settle_s + QUIET_HOLD_S:
        ap.error(
            f"--settle-timeout ({args.settle_timeout}) must exceed --settle-s "
            f"({args.settle_s}) plus the {QUIET_HOLD_S}s quiet window, or settling can "
            "never succeed"
        )
    # A recording path that cannot be opened should not cost a connection, and OSError
    # is not an AsimovError so it would escape the handler as a traceback.
    if args.record:
        try:
            with open(args.record, "w", encoding="utf-8"):
                pass
        except OSError as exc:
            ap.error(f"--record {args.record!r} cannot be written: {exc}")

    stages = [("connect", stage_connect), ("stand", stage_stand), ("walk", stage_walk)]
    stages = stages[: [n for n, _ in stages].index(args.until) + 1]

    print(f"connecting to {args.host} …")
    try:
        robot = Robot.connect(args.host)
    except ConnectError as exc:
        print(f"FAIL connect: {exc}")
        return 2

    failed: str | None = None
    progress = Progress()
    # Order matters: the ExitStack unwinds before `robot`, so the recording must be
    # entered FIRST and the robot INSIDE it. The other way round, Recording.__exit__
    # detaches the hook before robot.close() sends its safety zero, and the log's last
    # word is the nonzero setpoint the SDK went out of its way to avoid misreporting
    # (robot.py:684-687) — in a file whose --record help says "every state and command".
    with contextlib.ExitStack() as stack:
        if args.record:
            stack.enter_context(robot.record(args.record))
            print(f"recording to {args.record}")
        stack.enter_context(robot)
        try:
            for _name, run in stages:
                run(robot, args, progress)
            if args.damp:
                stage_damp(robot, args, progress)
        except KeyboardInterrupt:
            failed = "aborted by the operator"
        except (CheckFailed, AsimovError) as exc:
            failed = str(exc)
        if failed is not None:
            print(f"\nFAIL {failed}")
            if not robot.connected:
                # The commonest hardware failure is the state stream dying, and that
                # makes `connected` False — so this was exactly the case where the old
                # cleanup silently did nothing and printed nothing at all.
                print("   the link is down: nothing could be sent. USE THE HARDWARE E-STOP.")
            elif args.damp_on_fail:
                # DAMP first. stop() is a MOVE command, and handing the walking policy
                # back to a robot that is already going down helps nobody; damp() clears
                # the SDK's latch itself, so a preceding stop() buys nothing.
                try:
                    robot.damp()
                    wait_damped(robot)
                    print("   damped (--damp-on-fail)")
                except (CheckFailed, AsimovError) as exc:
                    print(f"   DAMP MAY NOT HAVE TAKEN EFFECT: {exc} — use the E-stop")
            else:
                try:
                    if progress.velocity:
                        robot.stop()
                        print(f"   stopped; {describe(robot.state)}")
                    elif progress.commanded:
                        print(f"   left as commanded; {describe(robot.state)}")
                    else:
                        print(f"   nothing was sent to the robot; {describe(robot.state)}")
                except AsimovError as exc:
                    print(f"   could not report the final state: {exc}")
    if failed is None:
        print("\nall requested stages passed")
    return 0 if failed is None else 1


if __name__ == "__main__":
    sys.exit(main())
