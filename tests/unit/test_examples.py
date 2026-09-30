"""Every example runs against the fake edge, sends what it says it sends, and keeps its checks.

The examples are the code the documentation shows, so each one is executed here as a
script. A test changes a setting the way a user would, by editing the constant at the top
of the file (``settings=``). Two seams point the scripts at the fake edge: the ``UdpConfig``
they build (or the one ``Robot()`` builds from ``MENLO_UDP_HOST``) gets the fake edge's
ports, and a LiveKit room is the fake client from ``conftest``. The fake edge runs with
``firmware=True``, so stand, arming and MOVE behave as on the robot. The interactive
scripts (keyboard.py and the apps) take their key source as the argument of ``main()``;
here it is a scripted one.

Several tests remove nothing and add nothing to an example: they fail when a safety check
in it is removed (a preflight before a walk, a bounded hold per key press, a start key).
"""

from __future__ import annotations

import asyncio
import io
import re
import sys
import threading
import time
import urllib.error
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
        monkeypatch.syspath_prepend(str(EXAMPLES))  # check.py and keyboard.py
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
    """Stand the fake robot with stand.py, as a user would before walking."""
    run("stand.py")
    assert fw_edge.armed


# ── check ────────────────────────────────────────────────────────────────────


def test_check_lists_problems_and_sends_nothing(run, fw_edge):
    out = run("check.py")
    assert "robot mode DAMP, armed False" in out
    assert "ready to stand" in out
    assert "not ready to move:" in out and "wrong_mode" in out
    assert "Stand the robot first: python examples/stand.py" in out
    assert fw_edge.received == []


def test_check_sees_a_standing_robot_armed_as_walk_py_does(run, fw_edge):
    # The SDK counts the 0.5 s upright hold from its own samples. A snapshot taken at connect
    # says not_armed on a robot that has stood for minutes; check.py waits as walk.py does.
    fw_edge.set_mode("stand")
    out = run("check.py")
    assert "robot mode STAND, armed True" in out
    assert "ready to move" in out and "not ready to move" not in out
    assert fw_edge.received == []


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


def test_stand_refuses_a_robot_in_move(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("stand.py", code=1)
    assert "not ready to stand" in out and "wrong_mode" in out
    assert fw_edge.received == []


def test_walk_refuses_in_damp_and_points_at_stand(run, fw_edge):
    # Without the preflight in walk.py a velocity goes out to a robot in DAMP.
    out = run("walk.py", code=1)
    assert "not ready to move" in out
    assert "python examples/stand.py" in out
    assert fw_edge.received == []


def test_walk_refuses_a_latched_fault(run, fw_edge):
    fw_edge.fault()
    out = run("walk.py", code=1)
    assert "faulted" in out and "until the firmware restarts" in out
    assert "stand.py" not in out, "standing does not clear a latched fault"
    assert fw_edge.received == []


def test_walk_after_stand_walks_then_stops_and_never_stands(run, fw_edge):
    _armed(run, fw_edge)
    out = run("walk.py", settings={"DURATION_S": 1.0})
    vs = fw_edge.velocities()
    assert (0.3, 0.0, 0.0) in vs and vs[-1] == ZERO
    assert fw_edge.first_velocity_at is not None and fw_edge.armed_at is not None
    assert fw_edge.first_velocity_at >= fw_edge.armed_at
    assert fw_edge.modes() == ["stand"], "walk.py sent a posture change"
    assert "robot mode MOVE" in out


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


# ── keyboard ─────────────────────────────────────────────────────────────────


def test_keyboard_moves_only_when_ready_and_each_press_is_a_bounded_hold(run, fw_edge):
    keys = Keys(["w", "t", *idle(0.8), "w", *idle(1.2)])
    out = run("keyboard.py", keys=keys)
    # w in DAMP: preflight refuses, nothing is sent; t stands; w walks once armed.
    assert "not ready to move: wrong_mode, press t to stand" in out
    assert fw_edge.modes()[0] == "stand"
    assert fw_edge.first_velocity_at is not None and fw_edge.armed_at is not None
    assert fw_edge.first_velocity_at >= fw_edge.armed_at
    assert (0.3, 0.0, 0.0) in fw_edge.velocities()
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


def test_keyboard_stands_only_from_damp(run, fw_edge):
    fw_edge.set_mode("move")
    out = run("keyboard.py", keys=Keys(["t"]))
    assert "stand works only from DAMP" in out
    assert "stand" not in fw_edge.modes()


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


# ── joints ───────────────────────────────────────────────────────────────────


def test_move_joints_needs_the_robot_supported(run, fw_edge):
    run("move_joints.py", code=1)
    assert fw_edge.received == []


def test_move_joints_refuses_in_damp_and_points_at_stand(run, fw_edge):
    out = run("move_joints.py", settings={"ROBOT_SUPPORTED": True}, code=1)
    assert "not ready to run a trajectory" in out and "python examples/stand.py" in out
    assert fw_edge.received == []


def test_move_joints_bends_the_elbow_and_back(run, fw_edge):
    _armed(run, fw_edge)
    out = run("move_joints.py", settings={"ROBOT_SUPPORTED": True})
    elbow = 15  # L_Elbow
    targets = [round(c.all_trajectory.positions[elbow], 3) for c in _drives(fw_edge)]
    # goto() returns once every joint is within its default 0.05 rad of the target.
    assert 0.25 <= max(targets) <= 0.3 and 0.0 <= targets[-1] <= 0.05
    assert re.search(r"L_Elbow moved from 0\.00 to 0\.(2[5-9]|30) rad", out)
    modes = [m for m in fw_edge.modes() if m != "move"]  # a trajectory carries mode MOVE
    assert modes == ["stand"], "move_joints.py changes no posture itself"


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


def test_record_and_replay_records_and_reads_back(run, fw_edge, tmp_path):
    out = run("record_and_replay.py", settings={"SECONDS": 1.0})
    assert (tmp_path / "run.jsonl").stat().st_size > 0
    assert "and 0 commands" in out
    assert "robot modes seen: {'DAMP':" in out
    assert fw_edge.received == []


# ── apps ─────────────────────────────────────────────────────────────────────


def _hybrid_env(manager: FakeManager) -> dict[str, str]:
    return {
        "MENLO_UDP_HOST": "127.0.0.1",
        "MENLO_MANAGER_URL": manager.url,
        "MENLO_CREDENTIAL": CREDENTIAL,
        "MENLO_MODE": "hybrid",
    }


def _show_the_ball(camera: Callable[[Frame | None], None]) -> None:
    """A frame filled with magenta, the ball a little left of centre."""
    magenta = bytes([255, 0, 255]) * (20 * 10)
    camera(Frame(width=20, height=10, encoding="rgb8", data=magenta, stride_bytes=60))


def test_agent_room_moves_nothing_until_the_start_key(run, fw_edge, rooms, camera, manager):
    fw_edge.set_mode("move")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    out = run("apps/agent_room.py", env=env, keys=Keys(idle(0.5)))
    assert "view.jpg" not in out
    # Only the zero velocity quitting sends in MOVE; nothing else went out.
    assert set(fw_edge.velocities()) == {ZERO}, "the agent moved the robot before g"
    assert fw_edge.modes() == []


def test_agent_room_looks_and_walks_after_the_start_key(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("PIL")
    fw_edge.set_mode("move")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    out = run("apps/agent_room.py", env=env, keys=Keys(["g", *idle(2.5)]))
    assert "view.jpg" in out and "walking for 2.0 s" in out
    vs = fw_edge.velocities()
    assert (0.3, 0.0, 0.0) in vs and vs[-1] == ZERO
    assert fw_edge.modes() == []


def _first_motion(edge: FakeEdge) -> float:
    """When the first nonzero velocity arrived."""
    return next(
        at
        for c, at in zip(tuple(edge.received), tuple(edge.arrived), strict=False)
        if c.HasField("policy") and (c.policy.vx, c.policy.vy, c.policy.vyaw) != ZERO
    )


def _assert_the_walk_ended_by_itself(edge: FakeEdge, keys: Keys, seconds: float) -> None:
    """The walk sent motion, then its zero by ``seconds`` after it began, before quit sent
    one: an unbounded hold would keep re-sending the velocity until quit."""
    start, quit_at = _first_motion(edge), keys.last("x")
    late = edge.velocities_between(start + seconds + 0.25, quit_at)
    assert all(v == ZERO for v in late), late
    assert ZERO in edge.velocities_between(start, quit_at), "only quitting stopped the walk"


def test_agent_room_walks_for_the_seconds_asked_then_stops_before_quit(
    run, fw_edge, rooms, camera, manager
):
    pytest.importorskip("PIL")
    fw_edge.set_mode("move")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    keys = Keys(["g", *idle(3.5)])
    run("apps/agent_room.py", env=env, keys=keys)
    _assert_the_walk_ended_by_itself(fw_edge, keys, 2.0)


def test_agent_room_caps_a_walk_at_max_walk_s(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("PIL")
    fw_edge.set_mode("move")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    keys = Keys(["g", *idle(3.0)])
    out = run("apps/agent_room.py", env=env, keys=keys, settings={"MAX_WALK_S": 1.0})
    assert "walking for 1.0 s" in out  # the plan asks for 2.0 s
    _assert_the_walk_ended_by_itself(fw_edge, keys, 1.0)


class _HotOnStart(Keys):
    """Presses g, but an actuator overheats just before: the robot was ready at connect."""

    def __init__(self, edge: FakeEdge, script: Iterable[str | None]) -> None:
        super().__init__(script)
        self.edge = edge

    def __call__(self, timeout: float) -> str | None:
        if self.script and self.script[0] == "g":
            self.edge.state.joint_temp[3] = 75.0  # L_Knee, past the joint_hot threshold
            time.sleep(0.2)  # the SDK has seen the new state
        return super().__call__(timeout)


def test_agent_room_walk_tool_runs_the_preflight_on_every_call(
    run, fw_edge, rooms, camera, manager
):
    pytest.importorskip("PIL")
    fw_edge.set_mode("move")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    out = run("apps/agent_room.py", env=env, keys=_HotOnStart(fw_edge, ["g", *idle(1.0)]))
    assert "joint_hot" in out and "walking for" not in out
    assert set(fw_edge.velocities()) == {ZERO}, "the walk tool moved a robot that was not ready"


def test_agent_room_pause_stops_the_walk(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("PIL")
    fw_edge.set_mode("move")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    keys = Keys(["g", *idle(0.5), " ", *idle(1.0)])
    run("apps/agent_room.py", env=env, keys=keys)
    after = fw_edge.velocities_between(keys.last(" ") + 0.2)
    assert after == [] or set(after) == {ZERO}


def test_follow_the_ball_moves_nothing_until_the_start_key(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("numpy")
    fw_edge.set_mode("move")
    _show_the_ball(camera)
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=Keys(idle(0.5)))
    assert all(v == ZERO for v in fw_edge.velocities()), "it followed before g was pressed"


def test_follow_the_ball_turns_towards_the_ball_and_stops(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("numpy")
    fw_edge.set_mode("move")
    _show_the_ball(camera)
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=Keys(["g", *idle(1.0)]))
    vs = fw_edge.velocities()
    # The ball fills the frame, a little left of centre: turn left, do not walk into it.
    assert any(vyaw > 0 and vx == 0.0 for vx, _, vyaw in vs)
    assert vs[-1] == ZERO


def test_follow_the_ball_pauses_on_space(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("numpy")
    fw_edge.set_mode("move")
    _show_the_ball(camera)
    keys = Keys(["g", *idle(0.5), " ", *idle(1.0)])
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=keys)
    assert any(vyaw > 0 for _, _, vyaw in fw_edge.velocities())
    after = fw_edge.velocities_between(keys.last(" ") + 0.2)
    assert after and set(after) == {ZERO}


def _after_turning(fw_edge: FakeEdge, then: Callable[[], None]) -> threading.Thread:
    """Once the robot is turning towards the ball, call ``then`` (in a thread)."""

    def watch() -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if any(vyaw > 0 for _, _, vyaw in fw_edge.velocities()):
                then()
                return
            time.sleep(0.01)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def test_follow_the_ball_stops_when_the_camera_freezes(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("numpy")
    fw_edge.set_mode("move")
    _show_the_ball(camera)
    frozen: list[float] = []

    def freeze() -> None:
        camera(None)  # the last frame still shows the ball, but it only gets older
        frozen.append(time.monotonic())

    watcher = _after_turning(fw_edge, freeze)
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=Keys(["g", *idle(2.0)]))
    watcher.join()
    assert frozen, "the robot never turned towards the ball"
    # A frame older than 0.3 s is not acted on; allow a tick and a hold re-send past that.
    later = fw_edge.velocities_between(frozen[0] + 0.3 + 0.25)
    assert later and set(later) == {ZERO}


def test_follow_the_ball_stops_when_its_loop_stalls(
    run, fw_edge, rooms, camera, manager, monkeypatch
):
    pytest.importorskip("numpy")
    fw_edge.set_mode("move")
    _show_the_ball(camera)
    real = Frame.to_numpy
    stall: list[float] = []

    def to_numpy(self: Frame) -> object:
        if not stall and any(vyaw > 0 for _, _, vyaw in fw_edge.velocities()):
            stall.append(time.monotonic())
            time.sleep(1.5)  # the script is busy elsewhere, not sending
            stall.append(time.monotonic())
        return real(self)

    monkeypatch.setattr(Frame, "to_numpy", to_numpy)
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=Keys(["g", *idle(1.0)]))
    assert len(stall) == 2, "the robot never turned towards the ball"
    # Each step holds for 0.5 s at most: within that (and a keepalive tick) the zero goes
    # out and nothing but zero follows until the loop runs again.
    held = fw_edge.velocities_between(stall[0] + 0.5 + 0.2, stall[1])
    assert all(v == ZERO for v in held), held
    assert ZERO in fw_edge.velocities_between(stall[0], stall[1])


def test_follow_the_ball_damps_only_on_yes(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("numpy")
    fw_edge.set_mode("move")
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=Keys(["b", "n"]))
    assert "damp" not in fw_edge.modes()
    run("apps/follow_the_ball.py", env=_hybrid_env(manager), keys=Keys(["b", "y"]))
    assert fw_edge.modes() == ["damp"]


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
