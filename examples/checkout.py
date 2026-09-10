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

Exit code 0 = every requested stage passed; 1 = a stage failed (the reason is printed);
2 = could not connect.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import sys
import time

from asimov_sdk import AsimovError, ConnectError, Mode, Robot, State

STALE_S = 0.5  # a sample older than this means we are flying blind: stop


class CheckFailed(Exception):
    pass


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


def check_sample(s: State, tilt_limit: float, label: str) -> float | None:
    """One sample's go/no-go. Returns the tilt so callers can track the worst seen."""
    if s.age_s > STALE_S:
        raise CheckFailed(f"{label}: state is {s.age_s:.2f}s old — the robot stopped talking")
    if s.faulted:
        raise CheckFailed(f"{label}: fault — {[a.name for a in s.alerts]}")
    if s.upright is False:
        assert s.gravity is not None
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
        quiet_since = (quiet_since or now) if rate < args.quiet_rad_s else None
        if now - t_start >= args.settle_s and quiet_since is not None and now - quiet_since >= 0.5:
            return rate
        if now - t_start > args.settle_timeout:
            raise CheckFailed(
                f"{label}: body still moving {args.settle_timeout:.0f}s after the stop "
                f"(gyro {rate:.2f} rad/s)"
            )
        time.sleep(0.05)


def gate(args: argparse.Namespace, what: str, robot: Robot | None = None) -> None:
    """Announce the next motion, and with --confirm wait for the operator.

    `input()` can block for minutes. `set_velocity` enforces no posture, fault or tilt
    precondition of its own, so whatever was true when we printed the prompt is not
    known to be true when Enter is pressed — re-read the robot before moving.
    """
    print(f"\n== {what}")
    if args.confirm:
        input("   press Enter to proceed (Ctrl-C to abort) … ")
        if robot is not None:
            check_sample(robot.state, args.tilt_deg, "gate")


def stage_connect(robot: Robot, args: argparse.Namespace) -> None:
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


def stage_stand(robot: Robot, args: argparse.Namespace) -> None:
    gate(args, "stand — stiffen to the standing pose (no balancing in this mode)", robot)
    robot.stand()
    try:
        s = robot.wait_for(Mode.STAND, timeout=args.stand_timeout, stale_after=STALE_S)
    except AsimovError as exc:
        raise CheckFailed(
            f"stand: {exc} (a robot that was DAMPed after a fall keeps refusing STAND until "
            "the firmware restarts; on the simulator, restart the rig)"
        ) from exc
    print(f"   standing: {describe(s)}")
    worst = hold_still(robot, args.stand_s, args.tilt_deg, "stand", expect=Mode.STAND)
    print(f"   PASS stand — held {args.stand_s:.0f}s, worst tilt {worst:.1f}°")


def stage_walk(robot: Robot, args: argparse.Namespace) -> None:
    gate(args, f"walk — forward at {args.vx:.2f} m/s for {args.walk_s:.1f}s", robot)
    sent = robot.set_velocity(vx=args.vx, duration=args.walk_s)
    if sent.clamped:
        print(f"   note: the SDK clamped the request to {sent.command}")
    try:
        robot.wait_for(Mode.MOVE, timeout=5.0, stale_after=STALE_S)
        worst = 0.0
        t_end = time.monotonic() + args.walk_s + 0.5
        while time.monotonic() < t_end:  # the watchdog: the hold is bounded, we still watch
            t = check_sample(robot.state, args.tilt_deg, "walk")
            if t is not None:
                worst = max(worst, t)
            time.sleep(0.05)
    finally:
        robot.stop()  # zero velocity whatever happened; the robot keeps its feet
    rate = settle(robot, args, "walk/settle")
    print(f"   walked; worst tilt {worst:.1f}°, quiet at {rate:.2f} rad/s")
    if args.after_walk == "stand":
        # The one motion that used to skip its gate. This file's own header says a
        # free-standing biped asked to stiffen after walking tips over, so of every
        # command here it is the one an operator most needs to authorise.
        gate(args, "re-stand — stiffen after the walk; tips a free-standing biped", robot)
        check_sample(robot.state, args.tilt_deg, "walk/re-stand")
        print("   back to STAND")
        robot.stand()
        s = robot.wait_for(Mode.STAND, timeout=args.stand_timeout, stale_after=STALE_S)
        print(f"   {describe(s)}")
        hold_still(robot, args.stand_s, args.tilt_deg, "walk/re-stand", expect=Mode.STAND)
    else:
        print(f"   holding at zero velocity (MOVE at rest): {describe(robot.state)}")
        hold_still(robot, args.stand_s, args.tilt_deg, "walk/rest", expect=Mode.MOVE)
    print("   PASS walk")


def stage_damp(robot: Robot, args: argparse.Namespace) -> None:
    gate(args, "damp — motors go compliant NOW; the robot folds", robot)
    robot.damp()
    s = robot.wait_for(Mode.DAMP, timeout=5.0)
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
        "--min-soc", type=float, default=20.0, help="refuse below this battery %% (default 20)"
    )
    ap.add_argument(
        "--after-walk",
        choices=["rest", "stand"],
        default="rest",
        help="after the walk: stay in MOVE at zero velocity (rest) or request STAND (default rest)",
    )
    ap.add_argument(
        "--confirm", action="store_true", help="ask before every motion (use on the real robot)"
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

    stages = [("connect", stage_connect), ("stand", stage_stand), ("walk", stage_walk)]
    stages = stages[: [n for n, _ in stages].index(args.until) + 1]

    print(f"connecting to {args.host} …")
    try:
        robot = Robot.connect(args.host)
    except ConnectError as exc:
        print(f"FAIL connect: {exc}")
        return 2

    failed: str | None = None
    moved = False  # has any motion command gone out? decides what cleanup may send
    with robot, contextlib.ExitStack() as stack:
        if args.record:
            stack.enter_context(robot.record(args.record))
            print(f"recording to {args.record}")
        try:
            for _name, run in stages:
                if _name == "walk":
                    moved = True
                run(robot, args)
            if args.damp:
                stage_damp(robot, args)
        except KeyboardInterrupt:
            failed = "aborted by the operator"
        except (CheckFailed, AsimovError) as exc:
            failed = str(exc)
        if failed is not None:
            print(f"\nFAIL {failed}")
            with contextlib.suppress(AsimovError):
                if robot.connected:
                    # stop() is a zero-velocity POLICY command, i.e. mode=MOVE. Sending it
                    # after a connect-stage failure would switch the controller although
                    # nothing was ever authorised to move.
                    if moved:
                        robot.stop()
                    if args.damp_on_fail:
                        robot.damp()
                        robot.wait_for(Mode.DAMP, timeout=5.0)
                        print("   damped (--damp-on-fail)")
                    else:
                        print(
                            f"   {'stopped' if moved else 'no motion was sent'}; "
                            f"{describe(robot.state)}"
                        )
    print("\nall requested stages passed" if failed is None else "")
    return 0 if failed is None else 1


if __name__ == "__main__":
    sys.exit(main())
