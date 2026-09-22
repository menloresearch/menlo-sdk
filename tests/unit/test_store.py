"""The credential store and the zero-config lookup: Robot().connect() finds the robot in the
environment or in ~/.asimov/robots.toml, and connect(persist=True) puts it there — after a
connect that worked, never before."""

from __future__ import annotations

import os
import stat

import pytest

from asimov_sdk import (
    ConnectError,
    ConnectionConfig,
    LiveKitConfig,
    ManagerConfig,
    Robot,
    RobotStore,
    StoredRobot,
    UdpConfig,
)
from asimov_sdk.store import resolve_connection, robot_name, store_path
from tests.conftest import route_manager_rooms_to

CRED = "eyJpZCI6ImEwY2QxNmRiIn0.secret"


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# ── the file ─────────────────────────────────────────────────────────────────


def test_the_store_round_trips_and_is_the_users_alone(tmp_path):
    path = tmp_path / "home" / "robots.toml"
    store = RobotStore(path)
    assert len(store) == 0 and store.default is None and store.get() is None
    store.put(StoredRobot("menlo-0001", "192.168.22.32", CRED, room="robot-menlo-0001"))
    store.put(StoredRobot("office bot", "http://10.39.241.166:8080/", "other-cred"))
    assert _mode(path) == 0o600 and _mode(path.parent) == 0o700
    assert not path.with_name("robots.toml.tmp").exists()

    again = RobotStore(path)
    assert sorted(again.robots) == ["menlo-0001", "office bot"]
    assert again.default == "menlo-0001", "the first robot saved becomes the default"
    first = again.get()
    assert first is not None and first.manager_url == "http://192.168.22.32"  # normalised
    assert first.credential == CRED and first.room == "robot-menlo-0001"
    office = again.get("office bot")
    assert office is not None and office.manager_url == "http://10.39.241.166:8080"
    assert office.room is None
    assert CRED not in repr(first) and CRED not in repr(again.robots)
    assert 'default = "menlo-0001"' in path.read_text()


def test_use_remove_and_the_default_follow_each_other(tmp_path):
    store = RobotStore(tmp_path / "robots.toml")
    store.put(StoredRobot("a", "http://a", "ca"))
    store.put(StoredRobot("b", "http://b", "cb"))
    assert store.default == "a"
    store.use("b")
    assert RobotStore(store.path).default == "b"
    with pytest.raises(KeyError, match="no robot named 'zzz'"):
        store.use("zzz")
    assert store.remove("b") is not None and store.remove("b") is None
    reloaded = RobotStore(store.path)
    assert reloaded.default is None and list(reloaded.robots) == ["a"]
    assert reloaded.get() is not None, "one robot and no default: that robot"
    store.put(StoredRobot("c", "http://c", "cc"), default=False)
    assert store.default is None and store.get() is None, "two robots, no default: no guess"


def test_a_stored_robot_needs_a_name_and_a_credential():
    with pytest.raises(ValueError, match="needs a name"):
        StoredRobot(" ", "http://x", "c")
    with pytest.raises(ValueError, match="no credential"):
        StoredRobot("x", "http://x", "")
    assert robot_name("http://192.168.22.32", "robot-menlo-0001") == "menlo-0001"
    assert robot_name("http://192.168.22.32", "shared") == "shared"
    assert robot_name("192.168.22.32", None) == "192.168.22.32"
    assert robot_name("http://asimov.local:8080", None) == "asimov.local"


def test_a_broken_store_is_a_value_error_naming_the_file(tmp_path):
    path = tmp_path / "robots.toml"
    path.write_text("default = [\n")
    with pytest.raises(ValueError, match=r"not a valid robots\.toml"):
        RobotStore(path)
    path.write_text('[robots.x]\nmanager_url = "http://x"\n')
    with pytest.raises(ValueError, match=r"\[robots.x\] is incomplete"):
        RobotStore(path)


def test_a_missing_default_is_ignored_and_a_loose_mode_is_warned_about(tmp_path, caplog):
    path = tmp_path / "robots.toml"
    path.write_text('default = "gone"\n[robots.a]\nmanager_url = "http://a"\ncredential = "c"\n')
    path.chmod(0o644)
    store = RobotStore(path)
    assert store.default is None and store.get() is not None
    assert "should be 0600" in caplog.text
    store.save()
    assert _mode(path) == 0o600, "saving repairs the mode"


def test_asimov_home_moves_the_store(monkeypatch, tmp_path):
    monkeypatch.setenv("ASIMOV_HOME", str(tmp_path / "elsewhere"))
    assert store_path() == tmp_path / "elsewhere" / "robots.toml"
    RobotStore().put(StoredRobot("a", "http://a", "c"))
    assert (tmp_path / "elsewhere" / "robots.toml").exists()


# ── the lookup ───────────────────────────────────────────────────────────────


def test_the_environment_wins_then_the_store_then_a_clear_error(monkeypatch):
    with pytest.raises(ConnectError) as info:
        resolve_connection()
    for hint in ("ConnectionConfig", "ASIMOV_MANAGER_URL", "ASIMOV_CREDENTIAL", "asimov login"):
        assert hint in str(info.value)

    store = RobotStore()
    store.put(StoredRobot("menlo-0001", "http://192.168.22.32", CRED, room="robot-menlo-0001"))
    cfg = resolve_connection()
    assert isinstance(cfg.livekit, ManagerConfig) and cfg.livekit.url == "http://192.168.22.32"
    assert cfg.livekit.credential == CRED and cfg.available_modes() == ("livekit",)

    monkeypatch.setenv("ASIMOV_MANAGER_URL", "10.0.0.7:8080")
    monkeypatch.setenv("ASIMOV_CREDENTIAL", "env-cred")
    cfg = resolve_connection()
    assert isinstance(cfg.livekit, ManagerConfig)
    assert cfg.livekit.url == "http://10.0.0.7:8080" and cfg.livekit.credential == "env-cred"

    monkeypatch.delenv("ASIMOV_CREDENTIAL")
    with pytest.raises(ConnectError, match="ASIMOV_CREDENTIAL is not"):
        resolve_connection()


def test_asimov_robot_picks_a_named_entry_and_names_the_ones_it_has(monkeypatch):
    store = RobotStore()
    store.put(StoredRobot("a", "http://a", "ca"))
    store.put(StoredRobot("b", "http://b", "cb"))
    monkeypatch.setenv("ASIMOV_ROBOT", "b")
    cfg = resolve_connection()
    assert isinstance(cfg.livekit, ManagerConfig) and cfg.livekit.url == "http://b"
    monkeypatch.setenv("ASIMOV_ROBOT", "c")
    with pytest.raises(ConnectError, match=r"ASIMOV_ROBOT='c' names no robot.*it has: a, b"):
        resolve_connection()
    monkeypatch.delenv("ASIMOV_ROBOT")
    store.default = None
    store.save()
    with pytest.raises(ConnectError, match="2 robots and no default"):
        resolve_connection()


def test_a_robot_with_no_config_binds_to_the_environment_and_picks_the_only_lane(monkeypatch):
    monkeypatch.setenv("ASIMOV_MANAGER_URL", "http://10.0.0.7")
    monkeypatch.setenv("ASIMOV_CREDENTIAL", "env-cred")
    robot = Robot()
    assert robot.config.available_modes() == ("livekit",)
    assert robot.config.only_mode() == "livekit"
    assert not robot.connected
    with pytest.raises(
        ValueError, match=r"can connect on udp, hybrid, livekit; pass connect\(mode\)"
    ):
        Robot(
            ConnectionConfig(udp=UdpConfig("h"), livekit=LiveKitConfig("ws://x", "r", "t"))
        ).connect()
    with pytest.raises(ConnectError, match="no lane set"):
        Robot(ConnectionConfig()).connect()


def test_connect_with_no_mode_uses_the_one_lane_the_config_has(edge):
    cfg = ConnectionConfig(
        udp=UdpConfig(
            "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
        )
    )
    with Robot(cfg).connect(timeout=3.0) as robot:
        assert robot.info.transport == "udp"


# ── persisting ───────────────────────────────────────────────────────────────


def test_a_successful_connect_with_persist_saves_the_robot_under_its_serial(
    edge, manager, monkeypatch
):
    route_manager_rooms_to(edge, monkeypatch)
    cfg = ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential=CRED))
    with Robot(cfg).connect("livekit", timeout=3.0, persist=True):
        pass
    store = RobotStore()
    saved = store.get()
    assert (
        saved is not None and saved.name == "menlo-0042"
    )  # the manager's room is robot-menlo-0042
    assert saved.manager_url == manager.url and saved.credential == CRED
    assert saved.room == "robot-menlo-0042" and store.default == "menlo-0042"
    assert _mode(store.path) == 0o600

    # and now the zero-config path reaches the same robot
    robot = Robot()
    assert isinstance(robot.config.livekit, ManagerConfig)
    assert robot.config.livekit.url == manager.url
    with robot.connect(timeout=3.0):
        assert robot.info.transport == "livekit"


def test_asimov_persist_in_the_environment_does_the_same(edge, manager, monkeypatch):
    route_manager_rooms_to(edge, monkeypatch)
    monkeypatch.setenv("ASIMOV_PERSIST", "1")
    cfg = ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential=CRED))
    with Robot(cfg).connect("livekit", timeout=3.0):
        pass
    assert RobotStore().get("menlo-0042") is not None


def test_a_failed_connect_never_writes_the_store(edge, manager, monkeypatch):
    route_manager_rooms_to(edge, monkeypatch)
    RobotStore().put(
        StoredRobot("menlo-0042", manager.url, "the-good-one", room="robot-menlo-0042")
    )
    manager.status = 401  # the manager refuses this credential
    cfg = ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential="the-bad-one"))
    with pytest.raises(ConnectError):
        Robot(cfg).connect("livekit", timeout=1.0, persist=True)
    manager.status = 200
    edge.pushing = False  # the token is fine, the robot never answers
    with pytest.raises(ConnectError):
        Robot(cfg).connect("livekit", timeout=0.3, persist=True)
    kept = RobotStore().get("menlo-0042")
    assert kept is not None and kept.credential == "the-good-one"


def test_persist_needs_a_manager_and_says_so_before_any_io(edge):
    cfg = ConnectionConfig(
        udp=UdpConfig(
            "127.0.0.1", command_port=edge.command_port, state_bind=("127.0.0.1", edge.state_port)
        )
    )
    with pytest.raises(ValueError, match="needs a ManagerConfig"):
        Robot(cfg).connect("udp", persist=True)
    assert edge.received == [] and not store_path().exists()
