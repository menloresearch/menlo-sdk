"""Every example runs against the fake edge, sends what it says it sends, and keeps its checks.

The examples are the code the documentation shows, so each one is executed here as a
script. A test changes a setting the way a user would, by editing the constant at the top
of the file (``settings=``). Two seams point the scripts at the fake edge: the ``UdpConfig``
they build (or the one ``Robot()`` builds from ``MENLO_UDP_HOST``) gets the fake edge's
ports, and a LiveKit room is the fake client from ``conftest``. The fake edge runs with
``firmware=True``, so stand, arming and MOVE behave as on the robot. The interactive
script (keyboard.py) takes its key source as the argument of ``main()``; here it is a
scripted one.

Several tests remove nothing and add nothing to an example: they fail when a check of its
own is removed (guard.py's rule, the MOVE-only rule of a walk, a bounded hold per key press,
the question before a stand from MOVE or a rest).
"""

from __future__ import annotations

import asyncio
import io
import re
import sys
import threading
import time
import urllib.error
import wave
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

import menlo.asimov
from menlo.asimov import Frame, UdpConfig, connection, store
from menlo.asimov.transport.livekit import HybridTransport, LiveKitTransport
from tests.conftest import FakeEdge, FakeLiveKitClient, FakeManager

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
CREDENTIAL = "eyJpZCI6ImEwY2QxNmRiIn0.secret"
ZERO = (0.0, 0.0, 0.0)

Run = Callable[..., str]


class Keys:
    """A scripted key source for ``main(next_key)``: each item is a key, or ``None`` for a
    tick in which no key is pressed (it waits the tick out, as a terminal would). After the
    last item it presses ``x``. ``pressed`` records when each key was handed out."""

    def __init__(self, script: Iterable[str | None]) -> None:
        self.script = list(script)
        self.pressed: list[tuple[str, float]] = []

    def __call__(self, timeout: float) -> str | None:
        key = self.script.pop(0) if self.script else "x"
        if key is None:
            time.sleep(timeout)
            return None
        self.pressed.append((key, time.monotonic()))
        return key

    def last(self, key: str) -> float:
        return [at for k, at in self.pressed if k == key][-1]


def idle(seconds: float, tick: float = 0.1) -> list[None]:
    """Ticks with no key pressed, for about ``seconds``."""
    return [None] * round(seconds / tick)


@pytest.fixture
def fw_edge() -> Iterator[FakeEdge]:
    e = FakeEdge(firmware=True, state_hz=200.0)
    e.state.joint_temp.extend([35.0] * 25)
    e.state.base_ang_vel.extend([0.0, 0.0, 0.0])
    yield e
    e.close()


@pytest.fixture
def rooms(fw_edge, monkeypatch) -> list[FakeLiveKitClient]:
    """Every LiveKit room a config opens is the fake client over ``fw_edge``. Returns the
    clients, so a test can push camera frames and audio into them."""
    clients: list[FakeLiveKitClient] = []

    class Room(LiveKitTransport):
        def __init__(self, url, room, *, token, **kw):
            client = FakeLiveKitClient(fw_edge, token=token() if callable(token) else token)
            clients.append(client)
            super().__init__(url, room, token=token, client=client, **kw)

    class Hybrid(HybridTransport):
        def __init__(self, host, *, livekit_url, room, token, **kw):
            client = FakeLiveKitClient(fw_edge, carry_state=False)
            clients.append(client)
            super().__init__(
                host, livekit_url=livekit_url, room=room, token=token, client=client, **kw
            )

    monkeypatch.setattr(connection, "LiveKitTransport", Room)
    monkeypatch.setattr(connection, "HybridTransport", Hybrid)
    return clients


@pytest.fixture
def camera(rooms) -> Iterator[Callable[[Frame | None], None]]:
    """Feed frames and audio into every joined room at about 30 Hz while a test runs.
    Call the returned function to change the frame that is fed, or with ``None`` to stop
    feeding frames (a frozen camera)."""
    frame: list[Frame | None] = [
        Frame(width=4, height=2, encoding="rgb8", data=bytes(24), stride_bytes=12)
    ]
    stop = threading.Event()

    def feed() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            for client in tuple(rooms):
                f = frame[0]
                if client.connected and f is not None:
                    live = Frame(f.width, f.height, f.encoding, f.data, f.stride_bytes, sequence=n)
                    for cb in tuple(client._video_cbs):
                        cb(live)
                    client.push_audio(n, samples=480)
            time.sleep(0.03)

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    yield lambda f: frame.__setitem__(0, f)
    stop.set()
    thread.join(timeout=1.0)


@pytest.fixture
def manager() -> Iterator[FakeManager]:
    server = FakeManager()
    yield server
    server.shutdown()


@pytest.fixture
def run(fw_edge, monkeypatch, tmp_path, capsys) -> Run:
    """Run ``examples/<name>`` and return what it printed.

    ``settings`` edits the constants at the top of the file, as a user would. ``keys``
    runs an interactive script's ``main()`` with a scripted key source. ``stdin`` is what
    the user types. The script must end with exit code ``code`` (finishing normally is 0).
    """

    def at_fake_edge(host: str, **_: object) -> UdpConfig:
        assert host == "127.0.0.1", "an example reached for a host other than the one given"
        return UdpConfig(
            host,
            command_port=fw_edge.command_port,
            state_bind=("127.0.0.1", fw_edge.state_port),
        )

    monkeypatch.setattr(menlo.asimov, "UdpConfig", at_fake_edge)
    monkeypatch.setattr(store, "UdpConfig", at_fake_edge)
    monkeypatch.chdir(tmp_path)

    def run(
        name: str,
        *,
        env: dict[str, str] | None = None,
        settings: dict[str, Any] | None = None,
        keys: Keys | None = None,
        stdin: str = "",
        code: int = 0,
    ) -> str:
        path = EXAMPLES / name
        source = path.read_text()
        for key, value in (settings or {}).items():
            pattern = rf"^{key}(: [^=]+)? = .*$"
            source, n = re.subn(pattern, f"{key} = {value!r}", source, flags=re.MULTILINE)
            assert n == 1, f"{name} has no setting {key}"
        for key, value in ({"MENLO_UDP_HOST": "127.0.0.1"} if env is None else env).items():
            monkeypatch.setenv(key, value)
        monkeypatch.syspath_prepend(str(path.parent))  # as `python <path>` puts it first
        monkeypatch.setattr(sys, "argv", [str(path)])
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        namespace = {"__name__": "__main__" if keys is None else "example", "__file__": str(path)}
        try:
            exec(compile(source, str(path), "exec"), namespace)
            if keys is not None:
                namespace["main"](keys)
        except SystemExit as exc:
            status = exc.code if isinstance(exc.code, int) or exc.code is None else 1
            assert (status or 0) == code, f"{name} exited with {exc.code!r}"
        else:
            assert code == 0, f"{name} finished; expected exit code {code}"
        return capsys.readouterr().out

    return run


def _drives(edge: FakeEdge) -> list:
    return [c for c in edge.received if c.HasField("policy") or c.HasField("all_trajectory")]


def _armed(run: Run, fw_edge: FakeEdge) -> None:
    """Stand the fake robot with stand.py, as a user would before balancing."""
    run("stand.py")
    assert fw_edge.armed


def _balancing(run: Run, fw_edge: FakeEdge) -> None:
    """stand.py, then balance.py, as a user would before walking."""
    _armed(run, fw_edge)
    run("balance.py")
    assert fw_edge.state.current_mode == 2  # MOVE


# ── check ────────────────────────────────────────────────────────────────────


def test_check_prints_the_facts_and_sends_nothing(run, fw_edge):
    a = fw_edge.state.active_alerts.add()
    a.id, a.severity = 17, 1  # MOTOR_TEMP_HIGH
    out = run("check.py")
    assert "robot mode DAMP, armed False, faulted False" in out
    assert "alerts MOTOR_TEMP_HIGH" in out and "hottest joint 35 C" in out
    assert "ready to stand" in out and "ready to move:" in out and "not ready" not in out
    assert "alerts: the firmware reports MOTOR_TEMP_HIGH (information)" in out
    assert fw_edge.received == []


def test_check_sees_a_standing_robot_armed(run, fw_edge):
    # The SDK counts the 0.5 s upright hold from its own samples. A snapshot taken at connect
    # says not_armed on a robot that has stood for minutes; check.py waits for it.
    fw_edge.set_mode("stand")
    out = run("check.py")
    assert "robot mode STAND, armed True" in out
    assert "ready to move" in out and "not_armed" not in out
    assert fw_edge.received == []


# ── guard ────────────────────────────────────────────────────────────────────


def test_guard_passes_a_healthy_robot_and_sends_nothing(run, fw_edge):
    out = run("guard.py")
    assert "guard: hottest joint" in out and "nothing stops this script" in out
    assert fw_edge.received == []


@pytest.mark.parametrize(
    ("setup", "says"),
    [
        (lambda e: e.state.joint_temp.__setitem__(15, 65.0), "L_Elbow is at 65 C"),
        (lambda e: e.fault(), "the firmware latched DAMP"),
        (
            lambda e: setattr(e.state.active_alerts.add(), "id", 17),
            "active alerts: MOTOR_TEMP_HIGH",
        ),
    ],
)  # fmt: skip
def test_guard_exits_on_your_rule_and_the_motion_scripts_call_it(run, fw_edge, setup, says):
    setup(fw_edge)
    for name in ("guard.py", "stand.py", "balance.py"):
        out = run(name, code=1)
        assert says in out and "guard: not going ahead" in out, name
    assert fw_edge.received == [], "the guard stops a script before it sends anything"


def test_guard_rule_is_yours_to_change(run, fw_edge):
    fw_edge.state.joint_temp[15] = 65.0
    assert "L_Elbow is at 65 C" in run("guard.py", code=1)
    out = run("guard.py", settings={"MAX_JOINT_TEMP_C": 70.0, "REFUSE_ON_ALERTS": False})
    assert "guard: hottest joint L_Elbow 65 C" in out and "nothing stops this script" in out


@pytest.mark.parametrize(
    ("name", "settings"),
    [
        ("stand.py", {}),
        ("balance.py", {}),
        ("walk.py", {}),
        ("stream_velocity.py", {}),
        ("move_joints.py", {"ROBOT_SUPPORTED": True}),
        ("wait_until.py", {"ROBOT_SUPPORTED": True}),
        ("keyboard.py", {}),
    ],
)
def test_every_motion_script_stops_on_the_guard_before_it_sends(run, fw_edge, name, settings):
    # In MOVE, supported where the script asks for it, and with a key pressed: each script
    # would send motion here, so only its guard(robot) call stops it.
    fw_edge.set_mode("move")
    fw_edge.state.joint_temp[15] = 65.0  # above guard.py's MAX_JOINT_TEMP_C
    keys = Keys(["w", *idle(0.3)]) if name == "keyboard.py" else None
    out = run(name, settings=settings, keys=keys, code=1)
    assert "L_Elbow is at 65 C" in out and "guard: not going ahead" in out
    assert _drives(fw_edge) == [] and fw_edge.received == [], "nothing was sent"


# ── connect ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["udp", "hybrid", "livekit"])
def test_connect_in_each_mode_reports_and_sends_nothing(run, fw_edge, rooms, manager, mode):
    settings = {"MODE": mode, "ROBOT_ADDRESS": "127.0.0.1", "MANAGER_URL": manager.url}
    out = run("connect.py", settings=settings, env={"MENLO_CREDENTIAL": CREDENTIAL})
    assert f"connected over {mode}" in out
    assert "robot mode DAMP, armed False" in out
    media = "camera" in out.split("capabilities:")[1]
    assert media is (mode != "udp")
    assert bool(rooms) is (mode != "udp")
    assert fw_edge.received == []


# ── state ────────────────────────────────────────────────────────────────────


def test_read_state_tours_the_state_and_sends_nothing(run, fw_edge):
    fw_edge.state.joint_temp[23] = 41.0  # Neck_Yaw
    out = run("read_state.py", settings={"STREAM_PERIOD_S": 0.2, "STREAM_LINES": 4})
    assert "robot mode   DAMP" in out and "faulted      False" in out
    assert "battery      not reported" in out
    assert "hottest      Neck_Yaw 41 °C" in out
    assert "upright True" in out
    lines = [line for line in out.splitlines() if line.startswith("DAMP ")]
    assert len(lines) == 4 and "rate" in lines[-1]
    assert fw_edge.received == []


def test_read_state_names_what_latched(run, fw_edge):
    fw_edge.fault()  # FALL_DETECTED
    out = run("read_state.py", settings={"STREAM_PERIOD_S": 0.05, "STREAM_LINES": 1})
    assert "faulted      True" in out and "FALL_DETECTED" in out
    assert fw_edge.received == []


# ── stand, walk, damp ────────────────────────────────────────────────────────


def test_stand_stands_from_damp_and_waits_until_armed(run, fw_edge):
    out = run("stand.py")
    assert fw_edge.modes() == ["stand"]
    assert fw_edge.armed and "robot mode STAND, armed True" in out
    assert _drives(fw_edge) == []


def test_stand_stands_from_move(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("stand.py")
    assert fw_edge.modes() == ["stand"] and "robot mode STAND, armed True" in out


def test_walk_keeps_its_own_move_only_rule_in_damp(run, fw_edge):
    out = run("walk.py", code=1)
    assert "robot mode DAMP: walk.py walks in MOVE only; run balance.py first" in out
    assert fw_edge.received == []


def test_walk_stops_on_a_latched_fault_by_the_guard(run, fw_edge):
    fw_edge.fault()
    out = run("walk.py", code=1)
    assert "the firmware latched DAMP" in out and "guard: not going ahead" in out
    assert fw_edge.received == []


def test_balance_enters_move_from_stand_and_waits_for_it(run, fw_edge):
    _armed(run, fw_edge)
    out = run("balance.py")
    assert fw_edge.velocities() == [ZERO], "balance() is one zero velocity"
    assert "robot mode MOVE, balancing in place" in out
    assert fw_edge.modes() == ["stand"]


def test_balance_in_damp_is_sent_and_says_stand_first(run, fw_edge):
    out = run("balance.py", code=1)
    assert "the robot is in DAMP: stand() first" in out
    assert fw_edge.velocities() == [ZERO], "sent: the firmware decides"


def test_balance_in_move_sends_zero(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("balance.py")
    assert fw_edge.velocities() == [ZERO] and "robot mode MOVE" in out


def test_walk_keeps_its_own_move_only_rule_in_stand(run, fw_edge):
    _armed(run, fw_edge)
    out = run("walk.py", code=1)
    assert "robot mode STAND: walk.py walks in MOVE only; run balance.py first" in out
    assert fw_edge.velocities() == [] and fw_edge.state.current_mode == 1  # STAND


def test_walk_after_balance_walks_then_balances_and_never_changes_mode(run, fw_edge):
    _balancing(run, fw_edge)
    fw_edge.received.clear()
    out = run("walk.py", settings={"DURATION_S": 1.0})
    vs = fw_edge.velocities()
    assert (0.3, 0.0, 0.0) in vs and vs[-1] == ZERO
    assert fw_edge.modes() == [], "walk.py sent a posture change"
    assert "robot mode MOVE, balancing in place" in out


def test_wait_until_needs_the_robot_supported(run, fw_edge):
    run("wait_until.py", code=1)
    assert fw_edge.received == []


def test_wait_until_acts_mid_move_once_the_joint_passes_the_angle(run, fw_edge):
    _armed(run, fw_edge)
    elbow = 15  # L_Elbow
    started = time.monotonic()
    out = run("wait_until.py", settings={"ROBOT_SUPPORTED": True, "DURATION_S": 1.0})
    m = re.search(r"L_Elbow at (\d\.\d\d) rad after (\d\.\d) s of 1\.0 s", out)
    assert m, out
    assert 0.3 <= float(m.group(1)) < 0.45, "it acted when the elbow passed, not at the end"
    assert float(m.group(2)) < 1.0, "it acted mid-move"
    targets = [round(c.all_trajectory.positions[elbow], 3) for c in _drives(fw_edge)]
    # The move back ends the first move where it is: the elbow never reached TURN_RAD.
    assert 0.3 <= max(targets) < 0.55 and targets[-1] <= 0.05, targets
    assert "L_Elbow back at 0.0" in out
    assert time.monotonic() - started < 10.0


def test_wait_until_in_stand_that_times_out_does_not_blame_damp(run, fw_edge, monkeypatch):
    fw_edge.set_mode("stand")  # the fake firmware moves joints in MOVE only: the wait times out
    wait_until = menlo.asimov.Robot.wait_until
    monkeypatch.setattr(
        menlo.asimov.Robot, "wait_until", lambda self, p, **kw: wait_until(self, p, timeout=0.3)
    )
    out = run("wait_until.py", settings={"ROBOT_SUPPORTED": True}, code=1)
    assert "within 0.3 s" in out
    assert "DAMP" not in out


def test_wait_until_in_damp_sends_and_says_the_robot_did_not_follow(run, fw_edge, monkeypatch):
    wait_until = menlo.asimov.Robot.wait_until
    monkeypatch.setattr(
        menlo.asimov.Robot, "wait_until", lambda self, p, **kw: wait_until(self, p, timeout=0.3)
    )
    out = run("wait_until.py", settings={"ROBOT_SUPPORTED": True}, code=1)
    assert "robot mode DAMP: the joints do not follow in DAMP, run stand.py first" in out
    assert _drives(fw_edge), "sent: the firmware drops a trajectory in DAMP"


def test_wait_until_moves_the_joint_in_move_too(run, fw_edge):
    _balancing(run, fw_edge)
    n = len(fw_edge.received)
    out = run("wait_until.py", settings={"ROBOT_SUPPORTED": True, "DURATION_S": 1.0})
    assert "L_Elbow at" in out and "L_Elbow back at" in out
    assert any(c.HasField("all_trajectory") for c in fw_edge.received[n:])


def test_stream_velocity_sends_one_packet_per_tick_then_balances(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("stream_velocity.py", settings={"DURATION_S": 1.0})
    packets = int(re.search(r"sent (\d+) packets", out).group(1))  # type: ignore[union-attr]
    assert 25 <= packets <= 51, "one packet per 50 Hz tick, at most"
    # The script has closed its robot: every packet it sent is on its way to the fake edge.
    assert fw_edge.wait_for(lambda r: sum(c.HasField("policy") for c in r) >= packets + 1)
    vs = fw_edge.velocities()
    # One packet per tick and the zero from balance(): nothing re-sent in the background.
    assert len(vs) == packets + 1 and vs[-1] == ZERO
    assert all(0.0 <= vx <= 0.2 for vx, _, _ in vs) and max(vx for vx, _, _ in vs) > 0.15
    assert "robot mode MOVE" in out


def test_stream_velocity_keeps_its_own_move_only_rule(run, fw_edge):
    _armed(run, fw_edge)
    out = run("stream_velocity.py", code=1)
    assert "robot mode STAND: this script streams in MOVE only" in out
    assert fw_edge.velocities() == []


def test_damp_asks_and_sends_nothing_on_no(run, fw_edge):
    out = run("damp.py", stdin="n\n")
    assert "Damp now? [y/N]" in out and "nothing sent" in out
    assert fw_edge.received == []


def test_damp_damps_on_yes(run, fw_edge):
    fw_edge.set_mode("stand")
    run("damp.py", stdin="y\n")
    assert fw_edge.modes() == ["damp"]
    assert fw_edge.state.current_mode == 0  # DAMP


def test_damp_without_asking_only_when_yes_is_set(run, fw_edge):
    fw_edge.set_mode("stand")
    out = run("damp.py", settings={"YES": True})  # no input: a question would get EOF
    assert "Damp now?" not in out
    assert fw_edge.modes() == ["damp"]


def test_damp_says_what_to_do_when_damp_is_not_reported(run, fw_edge, monkeypatch):
    damp = menlo.asimov.Robot.damp
    monkeypatch.setattr(menlo.asimov.Robot, "damp", lambda self, **_kw: damp(self, timeout=0.3))
    fw_edge.firmware = False  # another controller holds the robot: DAMP has no effect
    fw_edge.set_mode("stand")
    out = run("damp.py", settings={"YES": True}, code=1)
    assert "sent DAMP, but the robot still reports STAND" in out
    assert "E-Stop in Asimov Manager" in out
    assert fw_edge.modes() == ["damp"]


def test_rest_asks_and_sends_nothing_on_no(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("rest.py", stdin="n\n")
    assert "Is the robot on its gantry hook or seated on a stool or bench? [y/N]" in out
    assert "nothing sent" in out and fw_edge.received == []


def test_rest_asks_then_sends_stand_then_damp(run, fw_edge):
    _balancing(run, fw_edge)
    fw_edge.received.clear()
    out = run("rest.py", stdin="y\n")
    assert fw_edge.modes() == ["stand", "damp"]
    assert (
        out.index("robot mode MOVE") < out.index("robot mode STAND") < out.index("robot mode DAMP")
    )
    assert fw_edge.state.current_mode == 0  # DAMP


def test_rest_without_asking_only_when_yes_is_set_and_leaves_damp_alone(run, fw_edge):
    out = run("rest.py")  # DAMP already
    assert "already at rest; nothing sent" in out and fw_edge.received == []
    fw_edge.set_mode("move")
    out = run("rest.py", settings={"YES": True})  # no input: a question would get EOF
    assert "[y/N]" not in out and fw_edge.modes() == ["stand", "damp"]


# ── keyboard ─────────────────────────────────────────────────────────────────


def test_keyboard_sends_in_any_mode_and_each_press_is_a_bounded_hold(run, fw_edge):
    keys = Keys(["w", "t", " ", "w", *idle(1.2)])
    out = run("keyboard.py", keys=keys)
    # w in DAMP is sent and does nothing (the firmware drops it); t stands; space
    # balances (MOVE); w then walks.
    assert "w: vx +0.30 vy +0.00 vyaw +0.00, in DAMP: press t to stand" in out
    assert fw_edge.modes()[0] == "stand" and "standing, armed" in out
    assert "balance in place" in out
    assert fw_edge.state.current_mode == 2  # MOVE
    # One press holds for 0.3 s: with no further key the zero follows by itself. An
    # unbounded hold would keep re-sending 0.3 m/s at 10 Hz until quit.
    after_hold = fw_edge.velocities_between(keys.last("w") + 0.3 + 0.2)
    assert after_hold and set(after_hold) == {ZERO}, after_hold
    assert fw_edge.velocities()[-1] == ZERO
    assert "damp" not in fw_edge.modes()


def test_keyboard_keys_map_to_small_velocities(run, fw_edge):
    fw_edge.set_mode("move")
    run("keyboard.py", keys=Keys(["s", None, "a", None, "d", None, "q", None, "e", None, " "]))
    sent = [v for v in fw_edge.velocities() if v != ZERO]
    assert set(sent) == {(-0.3, 0, 0), (0, 0.3, 0), (0, -0.3, 0), (0, 0, 0.6), (0, 0, -0.6)}
    assert fw_edge.velocities()[-1] == ZERO


def test_keyboard_stand_in_move_asks_about_support_first(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("keyboard.py", keys=Keys(["t", "n"]))
    assert "gantry hook or seated on a stool or bench? [y/N]" in out
    assert "stand cancelled" in out and "stand" not in fw_edge.modes()
    out = run("keyboard.py", keys=Keys(["t", "y"]))
    assert fw_edge.modes() == ["stand"] and "standing, armed" in out


def test_keyboard_damps_only_on_yes(run, fw_edge):
    fw_edge.set_mode("move")
    run("keyboard.py", keys=Keys(["b", "n"]))
    assert "damp" not in fw_edge.modes()
    run("keyboard.py", keys=Keys(["b", "y"]))
    assert fw_edge.modes()[-1] == "damp"


def test_keyboard_quit_sends_zero_in_move(run, fw_edge):
    fw_edge.set_mode("move")
    run("keyboard.py", keys=Keys(["q", "x"]))
    assert fw_edge.velocities()[-1] == ZERO


def test_keyboard_space_in_damp_says_to_stand(run, fw_edge):
    out = run("keyboard.py", keys=Keys([" "]))
    assert "sent balance, but the robot reports DAMP, press t to stand" in out
    assert fw_edge.velocities() == [ZERO]


# ── joints ───────────────────────────────────────────────────────────────────


def test_move_joints_needs_the_robot_supported(run, fw_edge):
    run("move_joints.py", code=1)
    assert fw_edge.received == []


def test_move_joints_in_damp_sends_and_reports_that_the_joints_did_not_follow(run, fw_edge):
    out = run("move_joints.py", settings={"ROBOT_SUPPORTED": True, "DURATION_S": 0.1}, code=1)
    assert "did not come within" in out
    assert _drives(fw_edge), "sent: the firmware drops a trajectory in DAMP"


def test_move_joints_in_move_is_sent(run, fw_edge):
    _balancing(run, fw_edge)
    n = len(fw_edge.received)
    out = run("move_joints.py", settings={"ROBOT_SUPPORTED": True, "DURATION_S": 0.5})
    assert "L_Elbow moved from" in out
    assert any(c.HasField("all_trajectory") for c in fw_edge.received[n:])


def test_move_joints_bends_the_elbow_and_back(run, fw_edge):
    _armed(run, fw_edge)
    out = run("move_joints.py", settings={"ROBOT_SUPPORTED": True})
    elbow = 15  # L_Elbow
    targets = [round(c.all_trajectory.positions[elbow], 3) for c in _drives(fw_edge)]
    # set_joints() returns once every joint is within its default 0.05 rad of the target.
    assert 0.25 <= max(targets) <= 0.3 and 0.0 <= targets[-1] <= 0.05
    assert re.search(r"L_Elbow moved from 0\.00 to 0\.(2[5-9]|30) rad", out)
    modes = [m for m in fw_edge.modes() if m != "move"]  # a trajectory carries mode MOVE
    assert modes == ["stand"], "move_joints.py changes no posture itself"


def test_move_joints_reports_joints_that_do_not_follow(run, fw_edge, monkeypatch):
    """set_joints() sent the trajectory but the reported pose never reached it: the script says
    so and exits 1 instead of dying with a traceback."""
    import tests.conftest as conftest

    _armed(run, fw_edge)
    held = list(fw_edge.state.joint_pos)  # the joints stay where they are, whatever is sent
    monkeypatch.setattr(conftest, "ankle_coupling", lambda _p: held)
    out = run("move_joints.py", settings={"ROBOT_SUPPORTED": True, "DURATION_S": 0.1}, code=1)
    assert "did not come within" in out
    assert _drives(fw_edge), "the trajectory was sent; the wait for its effect timed out"


# ── media and recording ──────────────────────────────────────────────────────


def test_camera_and_audio_saves_a_photo_and_a_clip_and_plays_a_tone(
    run, fw_edge, rooms, camera, manager
):
    pytest.importorskip("PIL")
    env = {
        "MENLO_UDP_HOST": "127.0.0.1",
        "MENLO_MANAGER_URL": manager.url,
        "MENLO_CREDENTIAL": CREDENTIAL,
        "MENLO_MODE": "hybrid",
    }
    out = run("camera_and_audio.py", env=env, settings={"CLIP_S": 1.0})
    assert Path("photo.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert Path("clip.wav").read_bytes()[:4] == b"RIFF"
    frames = sorted(Path("clip").glob("*.jpg"))
    assert frames and frames[0].read_bytes()[:2] == b"\xff\xd8"
    assert f"saved {len(frames)} frames to clip/" in out and "played 1 s at 440 Hz" in out
    assert sum(c.samples_per_channel for c in rooms[0].played) == 16_000
    assert fw_edge.received == []


MEDIA_ENV = {
    "MENLO_UDP_HOST": "127.0.0.1",
    "MENLO_CREDENTIAL": CREDENTIAL,
    "MENLO_MODE": "hybrid",
}


def test_record_audio_saves_the_microphone_as_a_wav_file(run, fw_edge, rooms, camera, manager):
    env = {**MEDIA_ENV, "MENLO_MANAGER_URL": manager.url}
    out = run("record_audio.py", env=env, settings={"SECONDS": 0.5})
    with wave.open("microphone.wav", "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16_000)
        assert wav.getnframes() >= 8_000, "at least SECONDS of audio"
    assert re.search(r"saved microphone\.wav, 0\.[5-9] s at 16000 Hz", out)
    assert fw_edge.received == []


def test_record_audio_says_when_the_connection_carries_no_microphone(run, fw_edge):
    out = run("record_audio.py", code=1)  # udp
    assert "carries no microphone" in out
    assert not Path("microphone.wav").exists()


def _write_wav(path: str, *, frames: int, rate: int = 8_000, width: int = 2) -> None:
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames * width))


def test_play_audio_sends_the_file_to_the_speaker_in_order(run, fw_edge, rooms, manager):
    _write_wav("hello.wav", frames=12_000)  # 1.5 s at 8 kHz
    out = run("play_audio.py", env={**MEDIA_ENV, "MENLO_MANAGER_URL": manager.url})
    played = rooms[0].played
    assert [c.samples_per_channel for c in played] == [8_000, 4_000], "CHUNK_S pieces, in order"
    assert {(c.sample_rate_hz, c.channels, c.encoding) for c in played} == {(8_000, 1, "pcm_s16le")}
    assert "played hello.wav, 1.5 s at 8000 Hz" in out
    assert fw_edge.received == []


def test_play_audio_plays_a_tone_when_there_is_no_file(run, fw_edge, rooms, manager):
    out = run("play_audio.py", env={**MEDIA_ENV, "MENLO_MANAGER_URL": manager.url})
    played = rooms[0].played
    assert [c.samples_per_channel for c in played] == [16_000], "one second, one piece"
    assert "played a 440 Hz tone (no hello.wav here), 1.0 s at 16000 Hz" in out
    assert fw_edge.received == []


def test_play_audio_refuses_a_file_that_is_not_16_bit_pcm(run, fw_edge, rooms, manager):
    _write_wav("hello.wav", frames=800, width=1)
    out = run("play_audio.py", env={**MEDIA_ENV, "MENLO_MANAGER_URL": manager.url}, code=1)
    assert "hello.wav is not 16-bit PCM" in out
    assert rooms[0].played == []


def test_record_and_replay_records_and_reads_back(run, fw_edge, tmp_path):
    out = run("record_and_replay.py", settings={"SECONDS": 1.0})
    assert (tmp_path / "run.jsonl").stat().st_size > 0
    assert "and 0 commands" in out
    assert "robot modes seen: {'DAMP':" in out
    assert fw_edge.received == []


# ── without the SDK ──────────────────────────────────────────────────────────


def _load(name: str, monkeypatch) -> dict[str, Any]:
    """Define what a livekit_raw script defines, without running its main()."""
    path = EXAMPLES / name
    monkeypatch.syspath_prepend(str(path.parent))
    namespace: dict[str, Any] = {"__name__": "example", "__file__": str(path)}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    return namespace


def test_manager_token_fetches_a_grant(manager, monkeypatch):
    fetch_grant = _load("livekit_raw/manager_token.py", monkeypatch)["fetch_grant"]
    manager.reply["url"] = "ws://localhost:7880"
    grant = fetch_grant(manager.url, CREDENTIAL)
    assert grant["room"] == "robot-menlo-0042"
    assert grant["url"] == "ws://127.0.0.1:7880"  # the robot's localhost is the manager's host
    headers, _ = manager.requests[0]
    assert headers["Authorization"] == f"Bearer {CREDENTIAL}"


def test_manager_token_refuses_a_redirect(manager, monkeypatch):
    fetch_grant = _load("livekit_raw/manager_token.py", monkeypatch)["fetch_grant"]
    elsewhere = FakeManager()
    try:
        manager.redirect_to = elsewhere.url + "/api/livekit/token"
        with pytest.raises(urllib.error.HTTPError):
            fetch_grant(manager.url, CREDENTIAL)
        assert elsewhere.requests == [], "the credential followed the redirect"
    finally:
        elsewhere.shutdown()


def test_manager_token_needs_a_credential(monkeypatch):
    fetch_grant = _load("livekit_raw/manager_token.py", monkeypatch)["fetch_grant"]
    with pytest.raises(SystemExit, match="MENLO_CREDENTIAL"):
        fetch_grant("http://127.0.0.1:9", "")


def _raw_state(**fields: Any) -> Any:
    """A healthy RobotState of a robot in DAMP, with ``fields`` changed."""
    from asimov_protocol.v1 import asimov_state_pb2

    state = asimov_state_pb2.RobotState(protocol_version=1, current_mode=0)
    state.joint_temp.extend([35.0] * 25)
    state.battery.soc_percent, state.battery.voltage_v = 80.0, 50.0
    for name, value in fields.items():
        if name == "critical_alert":
            alert = state.active_alerts.add()
            alert.id, alert.severity = 7, 0
        elif name == "hot":
            state.joint_temp[3] = 70.0
        elif name.startswith("battery_"):
            setattr(state.battery, name.removeprefix("battery_"), value)
        else:
            setattr(state, name, value)
    return state


class _RawRoom:
    """The part of ``livekit.rtc.Room`` send_commands.py uses: it publishes the robot's
    "state" data track on connect, and records every packet published."""

    def __init__(self, state: Any, *, frames: int | None) -> None:
        self.state, self.frames = state, frames  # frames=None: a live stream
        self.published: list[tuple[bytes, str]] = []
        self.callbacks: dict[str, Callable[..., None]] = {}

    def on(self, event: str, callback: Callable[..., None]) -> None:
        self.callbacks[event] = callback

    async def connect(self, url: str, token: str) -> None:
        room = self

        class Track:
            info = type("Info", (), {"name": "state"})()

            async def subscribe(self) -> Any:
                n = 0
                while room.frames is None or n < room.frames:
                    n += 1
                    yield type("F", (), {"payload": room.state.SerializeToString()})()
                    await asyncio.sleep(0.01)
                await asyncio.Event().wait()  # the stream goes quiet

        self.callbacks["data_track_published"](Track())

    @property
    def local_participant(self) -> Any:
        room = self

        class Participant:
            async def publish_data(self, payload: bytes, *, reliable: bool, topic: str) -> None:
                room.published.append((payload, topic))

        return Participant()

    async def disconnect(self) -> None:
        pass


def _send_commands(monkeypatch, state: Any, *, frames: int | None = None, listen_s=0.1):
    """Run send_commands.py's main() against a room carrying ``state``."""
    script = _load("livekit_raw/send_commands.py", monkeypatch)
    room = _RawRoom(state, frames=frames)
    rtc = type("rtc", (), {"Room": lambda: room, "RemoteDataTrack": object})
    script.update(rtc=rtc, fetch_grant=lambda: {"url": "ws://x", "token": "t"}, LISTEN_S=listen_s)
    code = asyncio.run(script["main"]())
    return code, room.published


def test_send_commands_sends_stand_to_a_ready_robot(monkeypatch, capsys):
    from asimov_protocol.v1 import asimov_command_pb2

    code, published = _send_commands(monkeypatch, _raw_state())
    assert code == 0 and len(published) == 1
    payload, topic = published[0]
    sent = asimov_command_pb2.RobotCommand.FromString(payload)
    assert topic == "commands" and sent.protocol_version == 1 and sent.mode == 1


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"current_mode": 2}, "not in DAMP"),
        ({"error_flags": 1 | 1 << 8}, "fault"),
        ({"critical_alert": True}, "critical alert"),
        ({"battery_protection_flags": 4}, "protecting"),
        ({"battery_soc_percent": 12.0}, "battery is at 12 %"),
        ({"protocol_version": 2}, "protocol 2"),
        ({"hot": True}, "actuator"),
    ],
)
def test_send_commands_publishes_nothing_to_a_robot_not_ready(monkeypatch, capsys, fields, reason):
    code, published = _send_commands(monkeypatch, _raw_state(**fields))
    assert code == 1 and published == [], "STAND went out to a robot that was not ready"
    assert reason in capsys.readouterr().out


def test_send_commands_publishes_nothing_on_stale_state(monkeypatch, capsys):
    # One sample, then the stream goes quiet for longer than MAX_STATE_AGE_S.
    code, published = _send_commands(monkeypatch, _raw_state(), frames=1, listen_s=0.7)
    assert code == 1 and published == []
    assert "older than 0.5 s" in capsys.readouterr().out


@pytest.mark.parametrize(
    "name", ["livekit_raw/read_state.py", "livekit_raw/camera.py", "livekit_raw/audio.py"]
)
def test_livekit_raw_room_scripts(name, monkeypatch):
    _load(name, monkeypatch)  # imports and definitions only
    pytest.skip(f"{name} joins a real LiveKit room with the robot's tracks; the fake has none")


def test_every_example_is_covered_here():
    names = {p.relative_to(EXAMPLES).as_posix() for p in EXAMPLES.rglob("*.py")}
    source = Path(__file__).read_text()
    missing = sorted(n for n in names if f'"{n}' not in source)
    assert not missing, missing
    for path in EXAMPLES.rglob("*.py"):
        compile(path.read_text(), str(path), "exec")  # every example at least parses


def test_every_example_marks_the_part_the_docs_show():
    for path in EXAMPLES.rglob("*.py"):
        text = path.read_text()
        assert text.count("# region main") == 1, path
        assert text.count("# endregion") == 1, path
        assert "\u2014" not in text, f"{path} has an em dash"
