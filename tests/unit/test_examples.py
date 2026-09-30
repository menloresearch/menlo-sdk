"""Every example runs, unchanged, against the fake edge, and sends what it says it sends.

The examples are the code the documentation shows, so each one is executed here as a
script (``runpy``, ``__name__ == "__main__"``). Two seams point them at the fake edge: the
``UdpConfig`` they build (or the one ``Robot()`` builds from ``MENLO_UDP_HOST``) gets the
fake edge's ports, and a LiveKit room is the fake client from ``conftest``. The fake edge
runs with ``firmware=True``, so stand, arming and MOVE behave as on the robot.
"""

from __future__ import annotations

import runpy
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import menlo.asimov
from menlo.asimov import Frame, UdpConfig, connection, store
from menlo.asimov.transport.livekit import HybridTransport, LiveKitTransport
from tests.conftest import FakeEdge, FakeLiveKitClient, FakeManager

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
CREDENTIAL = "eyJpZCI6ImEwY2QxNmRiIn0.secret"

Run = Callable[..., str]


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
def camera(rooms) -> Iterator[Callable[[Frame], None]]:
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
    """Run ``examples/<name>`` with ``argv`` and ``env``; return what it printed. The
    script must end with exit code ``code`` (finishing normally is 0)."""

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

    def run(name: str, *argv: str, env: dict[str, str] | None = None, code: int = 0) -> str:
        environ = {"MENLO_UDP_HOST": "127.0.0.1"} if env is None else env
        for key, value in environ.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(sys, "argv", [name, *argv])
        try:
            runpy.run_path(str(EXAMPLES / name), run_name="__main__")
        except SystemExit as exc:
            assert (exc.code or 0) == code, f"{name} exited with {exc.code}"
        else:
            assert code == 0, f"{name} finished; expected exit code {code}"
        return capsys.readouterr().out

    return run


def _drives(edge: FakeEdge) -> list:
    return [c for c in edge.received if c.HasField("policy") or c.HasField("all_trajectory")]


# ── connect ──────────────────────────────────────────────────────────────────


def test_01_connect_udp_reads_state_and_sends_nothing(run, fw_edge):
    out = run("01_connect_udp.py", "127.0.0.1")
    assert "robot mode DAMP, armed False" in out
    assert fw_edge.received == []


def test_02_connect_hybrid_reports_media_and_sends_nothing(run, fw_edge, rooms, manager):
    out = run(
        "02_connect_hybrid.py", "127.0.0.1", manager.url, env={"MENLO_CREDENTIAL": CREDENTIAL}
    )
    assert "camera True, microphone True" in out
    assert "robot mode DAMP" in out
    assert fw_edge.received == []


def test_03_connect_livekit_joins_the_room_and_sends_nothing(run, fw_edge, rooms, manager):
    out = run("03_connect_livekit.py", manager.url, env={"MENLO_CREDENTIAL": CREDENTIAL})
    assert "robot-menlo-0042@" in out
    assert "robot mode DAMP" in out
    assert rooms and fw_edge.received == []


# ── move safely ──────────────────────────────────────────────────────────────


def test_04_preflight_lists_problems_and_sends_nothing(run, fw_edge):
    out = run("04_preflight.py")
    assert "ready to stand" in out
    assert "not ready to move:" in out and "wrong_mode" in out
    assert "Stand the robot first" in out
    assert fw_edge.received == []


def test_05_stands_waits_until_armed_walks_and_stops(run, fw_edge):
    out = run("05_stand_and_walk.py")
    assert fw_edge.modes()[0] == "stand"
    vs = fw_edge.velocities()
    assert (0.2, 0.0, 0.0) in vs and vs[-1] == (0.0, 0.0, 0.0)
    # wait_ready("move") held the first velocity back until the firmware had armed.
    assert fw_edge.armed_at is not None and fw_edge.first_velocity_at is not None
    assert fw_edge.first_velocity_at >= fw_edge.armed_at
    assert "robot mode MOVE" in out


def test_05_does_not_stand_a_robot_that_is_already_walking(run, fw_edge):
    fw_edge.set_mode("move")
    run("05_stand_and_walk.py")
    assert "stand" not in fw_edge.modes()
    assert (0.2, 0.0, 0.0) in fw_edge.velocities()


def test_06_stop_ends_the_turn_and_leaves_the_robot_in_move(run, fw_edge):
    run("06_stop_and_shutdown.py")
    vs = fw_edge.velocities()
    assert (0.0, 0.0, 0.3) in vs and vs[-1] == (0.0, 0.0, 0.0)
    assert "damp" not in fw_edge.modes()
    assert fw_edge.state.current_mode == 2  # MOVE


def test_06_damps_only_when_asked(run, fw_edge):
    run("06_stop_and_shutdown.py", "--damp")
    assert fw_edge.modes()[-1] == "damp"
    assert fw_edge.state.current_mode == 0  # DAMP


# ── state, joints, recording ─────────────────────────────────────────────────


def test_07_read_state_prints_and_sends_nothing(run, fw_edge):
    fw_edge.state.joint_temp[23] = 41.0  # Neck_Yaw
    out = run("07_read_state.py")
    lines = [line for line in out.splitlines() if line.startswith("DAMP")]
    assert len(lines) == 10
    assert "hottest joint Neck_Yaw 41 °C" in lines[0] and "battery not reported" in lines[0]
    assert fw_edge.received == []


def test_08_refuses_without_supported(run, fw_edge):
    run("08_move_joints.py", code=2)
    assert fw_edge.received == []


def test_08_moves_the_head_and_back_then_damps(run, fw_edge):
    out = run("08_move_joints.py", "--supported")
    neck = 23  # Neck_Yaw
    targets = [round(c.all_trajectory.positions[neck], 3) for c in _drives(fw_edge)]
    # goto() returns once every joint is within the 0.01 rad tolerance the example asks for.
    assert 0.29 <= max(targets) <= 0.3 and 0.0 <= targets[-1] <= 0.01
    assert "Neck_Yaw at 0.29 rad" in out or "Neck_Yaw at 0.30 rad" in out
    modes = [m for m in fw_edge.modes() if m != "move"]  # a trajectory carries mode MOVE
    assert modes == ["stand", "damp"], "stand only from DAMP, never from MOVE"
    assert fw_edge.state.current_mode == 0  # DAMP


def test_10_records_and_reads_back(run, fw_edge, tmp_path):
    out = run("10_record_and_replay.py")
    assert (tmp_path / "run.jsonl").stat().st_size > 0
    assert "and 0 commands" in out
    assert "robot modes seen: {'DAMP':" in out
    assert fw_edge.received == []


# ── media ────────────────────────────────────────────────────────────────────


def test_09_saves_a_photo_and_a_clip_and_plays_a_tone(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("PIL")
    env = {
        "MENLO_UDP_HOST": "127.0.0.1",
        "MENLO_MANAGER_URL": manager.url,
        "MENLO_CREDENTIAL": CREDENTIAL,
        "MENLO_MODE": "hybrid",
    }
    out = run("09_camera_and_audio.py", env=env)
    assert Path("photo.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert Path("clip.wav").read_bytes()[:4] == b"RIFF"
    assert "frames at" in out
    played = rooms[0].played
    assert sum(c.samples_per_channel for c in played) == 16_000
    assert fw_edge.received == []


# ── apps ─────────────────────────────────────────────────────────────────────


def test_agent_room_looks_and_turns_then_stops(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("PIL")
    env = {"MENLO_MANAGER_URL": manager.url, "MENLO_CREDENTIAL": CREDENTIAL}
    out = run("apps/agent_room.py", env=env)
    assert "view.jpg" in out and "walked" in out
    vs = fw_edge.velocities()
    assert (0.0, 0.0, 0.4) in vs and vs[-1] == (0.0, 0.0, 0.0)
    assert fw_edge.modes()[0] == "stand" and "stand" not in fw_edge.modes()[1:]


def test_follow_the_ball_turns_towards_the_ball_and_stops(run, fw_edge, rooms, camera, manager):
    pytest.importorskip("numpy")
    magenta = bytes([255, 0, 255]) * (20 * 10)
    camera(Frame(width=20, height=10, encoding="rgb8", data=magenta, stride_bytes=60))
    env = {
        "MENLO_UDP_HOST": "127.0.0.1",
        "MENLO_MANAGER_URL": manager.url,
        "MENLO_CREDENTIAL": CREDENTIAL,
        "MENLO_MODE": "hybrid",
    }
    run("apps/follow_the_ball.py", "--seconds", "1.5", env=env)
    vs = fw_edge.velocities()
    # The ball fills the frame, a little left of centre: turn left, do not walk into it.
    assert any(vyaw > 0 and vx == 0.0 for vx, _, vyaw in vs)
    assert vs[-1] == (0.0, 0.0, 0.0)


def _follow_env(manager: FakeManager) -> dict[str, str]:
    return {
        "MENLO_UDP_HOST": "127.0.0.1",
        "MENLO_MANAGER_URL": manager.url,
        "MENLO_CREDENTIAL": CREDENTIAL,
        "MENLO_MODE": "hybrid",
    }


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
    magenta = bytes([255, 0, 255]) * (20 * 10)
    camera(Frame(width=20, height=10, encoding="rgb8", data=magenta, stride_bytes=60))
    frozen: list[float] = []

    def freeze() -> None:
        camera(None)  # the last frame still shows the ball, but it only gets older
        frozen.append(time.monotonic())

    watcher = _after_turning(fw_edge, freeze)
    run("apps/follow_the_ball.py", "--seconds", "2.5", env=_follow_env(manager))
    watcher.join()
    assert frozen, "the robot never turned towards the ball"
    # A frame older than 0.3 s is not acted on; allow a tick and a hold re-send past that.
    later = fw_edge.velocities_between(frozen[0] + 0.3 + 0.25)
    assert later and set(later) == {(0.0, 0.0, 0.0)}


def test_follow_the_ball_stops_when_its_loop_stalls(
    run, fw_edge, rooms, camera, manager, monkeypatch
):
    pytest.importorskip("numpy")
    magenta = bytes([255, 0, 255]) * (20 * 10)
    camera(Frame(width=20, height=10, encoding="rgb8", data=magenta, stride_bytes=60))
    real = Frame.to_numpy
    stall: list[float] = []

    def to_numpy(self: Frame) -> object:
        if not stall and any(vyaw > 0 for _, _, vyaw in fw_edge.velocities()):
            stall.append(time.monotonic())
            time.sleep(1.5)  # the script is busy elsewhere, not sending
            stall.append(time.monotonic())
        return real(self)

    monkeypatch.setattr(Frame, "to_numpy", to_numpy)
    run("apps/follow_the_ball.py", "--seconds", "2.5", env=_follow_env(manager))
    assert len(stall) == 2, "the robot never turned towards the ball"
    # Each tick holds for 0.5 s at most: within that (and a keepalive tick) the zero goes out
    # and nothing but zero follows until the loop runs again.
    held = fw_edge.velocities_between(stall[0] + 0.5 + 0.2, stall[1])
    assert all(v == (0.0, 0.0, 0.0) for v in held), held
    assert (0.0, 0.0, 0.0) in fw_edge.velocities_between(stall[0], stall[1])


@pytest.mark.parametrize("seconds", ["inf", "nan", "0", "-1", "601"])
def test_follow_the_ball_refuses_an_unbounded_run(run, fw_edge, seconds):
    run("apps/follow_the_ball.py", "--seconds", seconds, code=2)
    assert fw_edge.received == []


# ── without the SDK ──────────────────────────────────────────────────────────


@pytest.mark.skip(reason="11_raw_livekit.py needs a LiveKit server and Asimov Manager")
def test_11_raw_livekit():  # pragma: no cover - documents why it is not run here
    pass


def test_every_example_is_covered_here():
    names = {p.relative_to(EXAMPLES).as_posix() for p in EXAMPLES.rglob("*.py")}
    source = Path(__file__).read_text()
    missing = sorted(n for n in names if f'"{n}' not in source)
    assert not missing, missing
    for path in EXAMPLES.rglob("*.py"):
        compile(path.read_text(), str(path), "exec")  # every example at least parses
