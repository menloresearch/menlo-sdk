"""The ``menlo`` command line, without a terminal: parsing, --no-input, --json, the live
checks, the check before every motion command, and the saved-robots round trip.

Commands that talk to a robot reach the fake edge through two seams: ``config_for`` (the
connection a command uses) and ``udp_config`` (the one the checks use), because a saved
robot names a host and the edge's default ports, and the fake edge listens elsewhere.
"""

from __future__ import annotations

import io
import json
import threading
import time

import pytest

from menlo.asimov import ConnectionConfig, Robot, RobotStore, StoredRobot, UdpConfig
from menlo.cli import _common, _drive, _robots, build_parser, main
from tests.conftest import FakeEdge, route_manager_rooms_to

CRED = "eyJpZCI6ImEwY2QxNmRiIn0.secret"


def _udp(edge: FakeEdge) -> UdpConfig:
    return UdpConfig(
        "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
    )


@pytest.fixture
def at_edge(monkeypatch):
    """Point every command and every check at ``edge``."""

    def point(edge: FakeEdge) -> None:
        monkeypatch.setattr(
            _common, "config_for", lambda args: ConnectionConfig(udp=_udp(edge), name="lab")
        )
        monkeypatch.setattr(_robots, "udp_config", lambda host: _udp(edge))

    return point


@pytest.fixture
def fw_edge():
    e = FakeEdge(firmware=True, state_hz=200.0)
    yield e
    e.close()


# ── parsing ──────────────────────────────────────────────────────────────────


def test_global_flags_work_before_and_after_the_command():
    p = build_parser()
    a = p.parse_args(["--robot", "lab", "--mode", "udp", "status", "--json"])
    assert (a.robot, a.mode, a.json, a.command_name) == ("lab", "udp", True, "status")
    b = p.parse_args(["status", "--robot", "lab", "--mode", "hybrid", "--watch"])
    assert (b.robot, b.mode, b.watch) == ("lab", "hybrid", True)
    c = p.parse_args(["robots", "add", "lab", "--mode", "udp", "--udp", "h", "--no-input"])
    assert (c.command_name, c.name, c.mode, c.udp, c.no_input) == (
        "robots add",
        "lab",
        "udp",
        "h",
        True,
    )


def test_no_command_prints_help_and_login_is_gone(capsys):
    assert main([]) == 2
    out = capsys.readouterr().out
    assert "setup" in out and "status" in out and "login" not in out
    for gone in ("login", "logout", "use"):
        with pytest.raises(SystemExit) as info:
            main([gone, "x"])
        assert info.value.code == 2
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0


# ── menlo robots ─────────────────────────────────────────────────────────────


def test_robots_add_with_missing_flags_and_no_input_is_a_usage_error(capsys):
    assert main(["robots", "add", "lab", "--no-input"]) == 2
    assert "lab needs --mode" in capsys.readouterr().err
    assert main(["robots", "add", "lab", "--mode", "hybrid", "--udp", "h", "--no-input"]) == 2
    err = capsys.readouterr().err
    assert "connection mode hybrid needs --manager" in err and "--credential" in err
    assert len(RobotStore()) == 0


def test_robots_add_keeps_a_global_mode(capsys):
    hybrid = ["--mode", "hybrid", "--udp", "h", "--manager", "http://h", "--credential", CRED]
    assert main(["robots", "add", "lab", *hybrid, "--no-check"]) == 0
    assert main(["--mode", "udp", "robots", "add", "lab", "--no-check"]) == 0
    assert RobotStore().get("lab").mode == "udp"
    assert main(["robots", "add", "lab", "--mode", "hybrid", "--no-check"]) == 0
    assert main(["robots", "--mode", "udp", "add", "lab", "--no-check"]) == 0
    assert RobotStore().get("lab").mode == "udp"


def test_robots_add_with_malformed_limits_is_a_usage_error(capsys):
    args = ["robots", "add", "lab", "--mode", "udp", "--udp", "h", "--limits", "0.3,x"]
    assert main([*args, "--no-check"]) == 2
    assert "--limits" in capsys.readouterr().err
    assert len(RobotStore()) == 0


def test_robots_add_off_a_terminal_never_asks(capsys):
    assert main(["robots", "add", "lab", "--mode", "udp"]) == 2
    assert "needs --udp" in capsys.readouterr().err


def test_the_saved_robots_round_trip_and_json_never_shows_the_credential(capsys):
    assert main(["robots", "add", "lab", "--mode", "udp", "--udp", "10.0.0.2", "--no-check"]) == 0
    assert main(
        ["robots", "add", "lab", "--mode", "hybrid", "--manager", "10.0.0.2",
         "--credential", CRED, "--limits", "0.3,0.3,0.6", "--no-check"]
    ) == 0  # fmt: skip
    assert main(["robots", "add", "b", "--mode", "livekit", "--manager", "b", "--credential",
                 "cb", "--no-check"]) == 0  # fmt: skip
    capsys.readouterr()
    assert main(["robots", "--json"]) == 0
    out = capsys.readouterr().out
    assert CRED not in out
    data = json.loads(out)
    assert data["default"] == "lab" and [r["name"] for r in data["robots"]] == ["lab", "b"]
    lab = data["robots"][0]
    assert lab["mode"] == "hybrid" and lab["udp_host"] == "10.0.0.2", "--udp kept"
    assert lab["manager_url"] == "http://10.0.0.2" and lab["credential_saved"] is True
    assert lab["limits"] == {"vx": 0.3, "vy": 0.3, "vyaw": 0.6}

    saved = RobotStore().get("lab")
    assert saved is not None and saved.credential == CRED
    assert main(["robots"]) == 0
    table = capsys.readouterr().out
    assert "lab" in table and "hybrid" in table and CRED not in table

    assert main(["robots", "use", "b"]) == 0 and RobotStore().default == "b"
    assert main(["robots", "use", "zzz"]) == 1
    assert "no robot named 'zzz'" in capsys.readouterr().err
    assert main(["robots", "remove", "b"]) == 0
    assert "b" not in RobotStore() and RobotStore().default is None
    assert main(["robots", "remove", "b"]) == 1


def test_robots_add_checks_the_credential_udp_and_room_then_saves_the_room(
    edge, manager, monkeypatch, at_edge, capsys
):
    at_edge(edge)
    route_manager_rooms_to(edge, monkeypatch)
    manager.reply["role"] = "control"
    argv = ["robots", "add", "lab", "--mode", "hybrid", "--udp", "127.0.0.1",
            "--manager", manager.url, "--credential", CRED]  # fmt: skip
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "credential: accepted, role control, room robot-menlo-0042" in out
    assert "udp: state from 127.0.0.1" in out and "Hz" in out
    assert "livekit: room joined" in out
    assert CRED not in out
    saved = RobotStore().get("lab")
    assert saved is not None and saved.room == "robot-menlo-0042" and saved.mode == "hybrid"
    assert edge.received == [], "the checks send nothing to the robot"


def test_robots_add_with_a_refused_credential_saves_nothing(manager, capsys):
    manager.status = 401
    argv = ["robots", "add", "lab", "--mode", "livekit", "--manager", manager.url,
            "--credential", "bad"]  # fmt: skip
    assert main(argv) == 1
    out = capsys.readouterr()
    assert "credential: " in out.out and "HTTP 401" in out.out
    assert "livekit: not tried" in out.out and "was not saved" in out.err
    assert len(RobotStore()) == 0


def test_an_observe_credential_is_saved_with_a_warning(edge, manager, monkeypatch, capsys):
    route_manager_rooms_to(edge, monkeypatch)
    manager.reply["role"] = "observe"
    argv = ["robots", "add", "lab", "--mode", "livekit", "--manager", manager.url,
            "--credential", CRED]  # fmt: skip
    assert main(argv) == 0
    assert "role observe: it can watch, not drive over livekit" in capsys.readouterr().out
    assert RobotStore().get("lab") is not None


def test_a_silent_robot_fails_the_udp_check_with_what_to_fix(edge, monkeypatch, at_edge, capsys):
    at_edge(edge)
    edge.pushing = False
    monkeypatch.setattr(_robots, "UDP_TIMEOUT_S", 0.3)
    assert main(["robots", "add", "lab", "--mode", "udp", "--udp", "127.0.0.1"]) == 1
    out = capsys.readouterr().out
    assert "no state from 127.0.0.1" in out and "udp-state-host" in out and "udp-control" in out
    assert len(RobotStore()) == 0


# ── menlo setup ──────────────────────────────────────────────────────────────


class Script:
    """Answers the wizard's questions in order, and records them."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def _next(self, message: str) -> object:
        self.asked.append(message)
        return self.answers.pop(0)

    def text(self, message, *, default="", validate=None):
        answer = str(self._next(message)) or default
        assert validate is None or validate(answer) is None, (message, answer)
        return answer

    def select(self, message, choices, *, default):
        answer = str(self._next(message))
        assert answer in [value for value, _ in choices]
        return answer

    def secret(self, message, *, keep=False):
        return str(self._next(message))

    def confirm(self, message, *, default):
        return bool(self._next(message))


def test_setup_needs_a_terminal(capsys):
    assert main(["setup"]) == 2
    assert "needs a terminal" in capsys.readouterr().err


def test_the_wizard_asks_only_what_the_mode_needs_checks_and_saves(edge, at_edge, capsys):
    at_edge(edge)
    ask = Script("lab", "udp", "127.0.0.1")
    assert _robots.wizard(RobotStore(), ask) == 0
    assert ask.asked == ["Robot name", "Connection mode", "Robot address"]
    out = capsys.readouterr().out
    assert "udp: state from 127.0.0.1" in out and "Saved lab (default)" in out
    saved = RobotStore().get("lab")
    assert saved is not None and saved.mode == "udp" and saved.udp_host == "127.0.0.1"


def test_the_wizard_saves_nothing_when_a_check_fails_and_you_say_no(
    edge, monkeypatch, at_edge, capsys
):
    at_edge(edge)
    edge.pushing = False
    monkeypatch.setattr(_robots, "UDP_TIMEOUT_S", 0.3)
    ask = Script("lab", "udp", "127.0.0.1", False)
    assert _robots.wizard(RobotStore(), ask) == 1
    assert ask.asked[-1] == "Save anyway?" and "Nothing saved" in capsys.readouterr().out
    assert len(RobotStore()) == 0


def test_the_wizard_keeps_a_saved_credential_and_asks_about_the_default(manager, capsys):
    store = RobotStore()
    store.put(StoredRobot("other", mode="udp", udp_host="10.0.0.9"))
    store.put(StoredRobot("lab", manager_url=manager.url, credential=CRED, mode="livekit"))
    ask = Script("lab", "livekit", "", "", True)  # Enter keeps the URL and the credential
    assert _robots.wizard(store, ask, check=False) == 0
    assert ask.asked == [
        "Robot name",
        "Connection mode",
        "Asimov Manager URL",
        "SDK credential",
        "Make lab the default robot?",
    ]
    saved = RobotStore().get("lab")
    assert saved is not None and saved.credential == CRED and RobotStore().default == "lab"


# ── menlo status ─────────────────────────────────────────────────────────────


def test_status_json_reports_readiness_and_sends_nothing(edge, at_edge, capsys):
    at_edge(edge)
    assert main(["status", "--json"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["robot"] == "lab" and snap["mode"] == "udp" and snap["host"] == "127.0.0.1"
    assert snap["verdict"] == "NOT READY" and snap["robot_mode"] == "DAMP"
    assert "wrong_mode" in [p["code"] for p in snap["problems"]]
    assert snap["fresh"] is True and snap["state_rate_hz"] > 10 and snap["faults"] == []
    assert edge.received == []


def test_status_shows_a_fault_by_name(edge, at_edge, capsys):
    at_edge(edge)
    edge.state.error_flags = 1 | (1 << 8)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "FAULTED" in out and "FALL_DETECTED" in out and "lab" in out


# ── motion: not feasible, a plan, a yes ─────────────────────────────────────


class Answer(io.StringIO):
    """What the person at the terminal types; ``then`` runs when the answer is read."""

    def __init__(self, text: str, then=None) -> None:
        super().__init__(text)
        self.then = then
        self.read_at: float | None = None

    def readline(self, *args):
        self.read_at = time.monotonic()
        if self.then is not None:
            self.then()
        return super().readline(*args)


@pytest.fixture
def answer(monkeypatch):
    """A terminal to ask on, answered with ``text``."""
    from menlo.cli import _ui

    def type_(text: str, then=None) -> Answer:
        typed = Answer(text, then)
        monkeypatch.setattr(_ui, "interactive", lambda: True)
        monkeypatch.setattr("sys.stdin", typed)
        return typed

    return type_


def _armed_stand(edge: FakeEdge) -> None:
    with Robot(ConnectionConfig(udp=_udp(edge))).connect("udp", timeout=3.0) as robot:
        robot.stand()
        robot.wait_ready("move", timeout=3.0)


def test_the_yes_flag_is_shared_by_the_commands_that_send(capsys):
    p = build_parser()
    for argv in (
        ["stand", "-y"],
        ["walk", "--vx", "1", "--duration", "1", "--yes"],
        ["damp", "-y"],
    ):
        assert p.parse_args(argv).yes is True
    for argv in (["stop", "-y"], ["status", "--yes"], ["robots", "-y"]):
        with pytest.raises(SystemExit) as info:
            p.parse_args(argv)
        assert info.value.code == 2


def test_stand_prints_the_plan_asks_and_stands_on_yes(fw_edge, at_edge, answer, capsys):
    at_edge(fw_edge)
    answer("y\n")
    assert main(["stand"]) == 0
    out = capsys.readouterr().out
    assert (
        "lab (udp, 127.0.0.1) · DAMP · battery not reported → stand, then wait until armed" in out
    )
    assert "Proceed? [y/N]" in out and "Armed" in out
    assert fw_edge.modes() == ["stand"]


@pytest.mark.parametrize("typed", ["n\n", "\n", "", "nope\n"])
def test_anything_but_yes_cancels_and_sends_nothing(edge, at_edge, answer, capsys, typed):
    at_edge(edge)
    answer(typed)
    assert main(["damp"]) == 4
    out = capsys.readouterr().out
    assert "→ damp: every actuator goes limp and a standing robot folds" in out
    assert "the robot must be supported. Not an emergency stop" in out
    assert "Cancelled; nothing sent." in out
    assert edge.received == []


def test_no_terminal_and_no_yes_is_a_usage_error_and_sends_nothing(edge, at_edge, capsys):
    at_edge(edge)
    edge.set_mode("stand")
    assert main(["damp"]) == 2
    captured = capsys.readouterr()
    assert "pass --yes" in captured.err and "Not an emergency stop" in captured.out
    assert edge.received == []
    edge.set_mode("damp")  # the plain fake edge does not follow commands
    assert main(["damp", "-y"]) == 0
    assert edge.modes() == ["damp"]


def test_walk_plans_the_clamped_speed_and_yes_skips_the_question(fw_edge, at_edge, capsys):
    at_edge(fw_edge)
    _armed_stand(fw_edge)
    fw_edge.received.clear()
    started = time.monotonic()
    assert main(["walk", "--vx", "0.6", "--vyaw", "-0.2", "--duration", "0.4", "--yes"]) == 0
    out = capsys.readouterr().out
    assert (
        "lab (udp, 127.0.0.1) · STAND, armed · battery not reported → walk vx 0.40 m/s "
        "(asked 0.60), vy 0.00 m/s, vyaw -0.20 rad/s for 0.4 s, then stop"
    ) in out
    assert "Proceed?" not in out and "Stopped. Robot mode MOVE" in out
    assert fw_edge.move_entered_at is not None and fw_edge.move_entered_at > started
    velocities = fw_edge.velocities()
    assert (0.4, 0.0, -0.2) in velocities and velocities[-1] == (0.0, 0.0, 0.0)


def test_walk_waits_for_a_new_session_to_see_the_robot_armed(fw_edge, at_edge, answer, capsys):
    at_edge(fw_edge)
    _armed_stand(fw_edge)
    fw_edge.received.clear()
    answer("yes\n")
    assert main(["walk", "--vx", "0.2", "--duration", "0.3"]) == 0
    assert "STAND, armed" in capsys.readouterr().out
    assert (0.2, 0.0, 0.0) in fw_edge.velocities()


@pytest.mark.parametrize(
    ("setup", "argv", "says"),
    [
        (
            lambda e: None,
            ["walk", "--vx", "0.2", "--duration", "1"],
            "Not feasible: lab is in DAMP, not balancing. Run `menlo stand` first.",
        ),
        (
            lambda e: e.set_mode("move"),
            ["stand"],
            "Not feasible: lab is in MOVE, balancing. `stand` only runs from DAMP; "
            "end a walk with `menlo stop`.",
        ),
        (
            lambda e: e.fault(),
            ["walk", "--vx", "0.2", "--duration", "1"],
            "Not feasible: lab is in DAMP, faulted. The firmware latched DAMP (FALL_DETECTED); "
            "it stays latched until the firmware restarts.",
        ),
        (
            lambda e: e.fault(),
            ["stand"],
            "Not feasible: lab is in DAMP, faulted. The firmware latched DAMP (FALL_DETECTED)",
        ),
    ],
)
def test_not_feasible_says_why_and_the_fix_and_never_asks(
    fw_edge, at_edge, answer, capsys, setup, argv, says
):
    at_edge(fw_edge)
    setup(fw_edge)
    typed = answer("y\n")
    assert main(argv) == 3
    captured = capsys.readouterr()
    assert says in captured.err
    assert "Proceed?" not in captured.out and typed.read_at is None
    assert fw_edge.received == []


def test_walk_before_arming_is_not_feasible_and_says_how_it_arms(
    fw_edge, at_edge, answer, monkeypatch, capsys
):
    at_edge(fw_edge)
    fw_edge.set_mode("stand")
    fw_edge.state.projected_gravity[:] = [0.0, 0.6, -0.8]
    monkeypatch.setattr(_drive, "ARM_TIMEOUT_S", 0.3)
    typed = answer("y\n")
    assert main(["walk", "--vx", "0.1", "--duration", "1"]) == 3
    err = capsys.readouterr().err
    assert err.startswith("Not feasible: lab is in STAND, not armed. The robot is tilted 37 deg")
    assert err.rstrip().endswith("Run the command again once `menlo status` shows it armed.")
    assert typed.read_at is None and fw_edge.received == []


def test_stale_state_is_not_feasible(edge, at_edge, answer, monkeypatch, capsys):
    at_edge(edge)
    edge.set_mode("move")
    real = Robot.preflight

    def stale(self, action="move"):
        edge.pushing = False
        time.sleep(0.7)
        return real(self, action)

    monkeypatch.setattr(Robot, "preflight", stale)
    typed = answer("y\n")
    assert main(["walk", "--vx", "0.1", "--duration", "1"]) == 3
    err = capsys.readouterr().err
    assert err.startswith("Not feasible: no fresh state from lab: the latest state is")
    assert typed.read_at is None and edge.received == []


def test_walk_checks_again_after_the_answer_and_sends_nothing_if_it_changed(
    fw_edge, at_edge, answer, capsys
):
    at_edge(fw_edge)
    _armed_stand(fw_edge)
    fw_edge.received.clear()

    def the_robot_falls_while_you_read() -> None:
        fw_edge.fault()
        time.sleep(0.1)  # a few samples at 200 Hz

    answer("y\n", then=the_robot_falls_while_you_read)
    assert main(["walk", "--vx", "0.2", "--duration", "1"]) == 3
    captured = capsys.readouterr()
    assert "STAND, armed" in captured.out and "Proceed?" in captured.out
    assert "Not feasible: lab is in DAMP, faulted" in captured.err
    assert fw_edge.received == []


def test_stand_that_never_reaches_stand_is_not_ready(edge, at_edge, monkeypatch, capsys):
    at_edge(edge)  # this edge never changes robot mode
    monkeypatch.setattr(_drive, "STAND_TIMEOUT_S", 0.3)
    assert main(["stand", "--yes"]) == 3
    assert "STAND was not reported" in capsys.readouterr().err


def test_walk_needs_a_bounded_duration(capsys):
    with pytest.raises(SystemExit) as info:
        main(["walk", "--vx", "0.2"])
    assert info.value.code == 2
    assert main(["walk", "--vx", "0.2", "--duration", "11"]) == 2
    assert main(["walk", "--duration", "2"]) == 2
    assert "give --vx" in capsys.readouterr().err


def test_ctrl_c_during_a_walk_stops_the_robot(fw_edge, at_edge, monkeypatch):
    at_edge(fw_edge)
    fw_edge.set_mode("move")

    def interrupted(self, done, duration):
        time.sleep(0.2)
        raise KeyboardInterrupt

    monkeypatch.setattr(Robot, "_await_hold", interrupted)
    assert main(["walk", "--vx", "0.3", "--duration", "5", "--yes"]) == 130
    velocities = fw_edge.velocities()
    assert (0.3, 0.0, 0.0) in velocities and velocities[-1] == (0.0, 0.0, 0.0)


def test_a_fault_during_a_walk_is_reported_and_is_not_done(fw_edge, at_edge, capsys):
    at_edge(fw_edge)
    _armed_stand(fw_edge)
    fw_edge.move_entered_at = None

    def fall_once_walking() -> None:
        deadline = time.monotonic() + 5.0
        while fw_edge.move_entered_at is None and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)
        fw_edge.fault()

    fall = threading.Thread(target=fall_once_walking, daemon=True)
    fall.start()
    code = main(["walk", "--vx", "0.2", "--duration", "3", "--yes"])
    fall.join()
    assert code == 3
    assert fw_edge.move_entered_at is not None
    assert time.monotonic() - fw_edge.move_entered_at < 2.5  # the fault ended it, not 3 s
    captured = capsys.readouterr()
    assert "Stopped." not in captured.out
    assert "menlo walk: the walk ended after" in captured.err
    assert "the firmware latched DAMP (FALL_DETECTED)" in captured.err
    assert "until the firmware restarts" in captured.err


def test_stop_repeats_the_zero_only_while_the_robot_is_still_in_move(edge, at_edge, monkeypatch):
    at_edge(edge)
    edge.set_mode("move")
    real = Robot.stop

    def stop_then_another_controller_stands(self):
        sent = real(self)
        edge.set_mode("stand")  # e.g. the Cockpit takes over and stands the robot
        return sent

    monkeypatch.setattr(Robot, "stop", stop_then_another_controller_stands)
    assert main(["stop"]) == 0
    time.sleep(0.2)
    assert edge.velocities() == [(0.0, 0.0, 0.0)]


def test_stop_never_asks_and_sends_zero_only_in_move(edge, at_edge, capsys):
    at_edge(edge)  # no terminal and no --yes: stop still runs
    assert main(["stop"]) == 0
    assert "nothing to stop" in capsys.readouterr().out and edge.received == []
    edge.set_mode("move")
    assert main(["stop"]) == 0
    assert edge.wait_for(lambda rx: len(rx) >= 2)
    assert set(edge.velocities()) == {(0.0, 0.0, 0.0)}
