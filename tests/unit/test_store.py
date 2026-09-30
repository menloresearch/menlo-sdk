"""The credential store and the zero-config lookup: Robot().connect() finds the robot in the
environment or in ~/.menlo/robots.toml, and connect(persist=True) puts it there after a
connect that worked, never before."""

from __future__ import annotations

import os
import stat

import pytest

from menlo.asimov import (
    ConnectError,
    ConnectionConfig,
    Limits,
    LiveKitConfig,
    ManagerConfig,
    Robot,
    RobotStore,
    StoredRobot,
    UdpConfig,
)
from menlo.asimov.store import resolve_connection, robot_name, store_path
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
    with pytest.raises(ValueError, match="empty credential"):
        StoredRobot("x", "http://x", "")
    with pytest.raises(ValueError, match="unknown mode"):
        StoredRobot("x", "http://x", "c", mode="wifi")  # type: ignore[arg-type]
    assert robot_name("http://192.168.22.32", "robot-menlo-0001") == "menlo-0001"
    assert robot_name("http://192.168.22.32", "shared") == "shared"
    assert robot_name("192.168.22.32", None) == "192.168.22.32"
    assert robot_name("http://asimov.local:8080", None) == "asimov.local"


def test_a_broken_store_is_a_value_error_naming_the_file(tmp_path):
    path = tmp_path / "robots.toml"
    path.write_text("default = [\n")
    with pytest.raises(ValueError, match=r"not a valid robots\.toml"):
        RobotStore(path)
    path.write_text('[robots.x]\nroom = "r"\n')
    with pytest.raises(ValueError, match=r"\[robots.x\] is incomplete"):
        RobotStore(path)
    path.write_text('[robots.x]\nudp_host = "h"\n[robots.x.limits]\nspeed = 1\n')
    with pytest.raises(ValueError, match=r"\[robots.x\].limits has unknown keys: speed"):
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


def test_menlo_home_moves_the_store(monkeypatch, tmp_path):
    monkeypatch.setenv("MENLO_HOME", str(tmp_path / "elsewhere"))
    assert store_path() == tmp_path / "elsewhere" / "robots.toml"
    RobotStore().put(StoredRobot("a", "http://a", "c"))
    assert (tmp_path / "elsewhere" / "robots.toml").exists()


# ── the lookup ───────────────────────────────────────────────────────────────


def test_the_environment_wins_then_the_store_then_a_clear_error(monkeypatch):
    with pytest.raises(ConnectError) as info:
        resolve_connection()
    for hint in (
        "ConnectionConfig",
        "MENLO_UDP_HOST",
        "MENLO_MANAGER_URL",
        "MENLO_CREDENTIAL",
        "menlo setup",
    ):
        assert hint in str(info.value)

    store = RobotStore()
    store.put(StoredRobot("menlo-0001", "http://192.168.22.32", CRED, room="robot-menlo-0001"))
    cfg = resolve_connection()
    assert isinstance(cfg.livekit, ManagerConfig) and cfg.livekit.url == "http://192.168.22.32"
    assert cfg.livekit.credential == CRED and cfg.available_modes() == ("livekit",)

    monkeypatch.setenv("MENLO_MANAGER_URL", "10.0.0.7:8080")
    monkeypatch.setenv("MENLO_CREDENTIAL", "env-cred")
    cfg = resolve_connection()
    assert isinstance(cfg.livekit, ManagerConfig)
    assert cfg.livekit.url == "http://10.0.0.7:8080" and cfg.livekit.credential == "env-cred"

    monkeypatch.delenv("MENLO_CREDENTIAL")
    with pytest.raises(ConnectError, match="MENLO_CREDENTIAL is not"):
        resolve_connection()


def test_menlo_robot_picks_a_named_entry_and_names_the_ones_it_has(monkeypatch):
    store = RobotStore()
    store.put(StoredRobot("a", "http://a", "ca"))
    store.put(StoredRobot("b", "http://b", "cb"))
    monkeypatch.setenv("MENLO_ROBOT", "b")
    cfg = resolve_connection()
    assert isinstance(cfg.livekit, ManagerConfig) and cfg.livekit.url == "http://b"
    monkeypatch.setenv("MENLO_ROBOT", "c")
    with pytest.raises(ConnectError, match=r"MENLO_ROBOT='c' names no saved robot.*it has: a, b"):
        resolve_connection()
    monkeypatch.delenv("MENLO_ROBOT")
    store.default = None
    store.save()
    with pytest.raises(ConnectError, match="2 robots and no default"):
        resolve_connection()


def test_a_robot_with_no_config_binds_to_the_environment_and_picks_the_only_lane(monkeypatch):
    monkeypatch.setenv("MENLO_MANAGER_URL", "http://10.0.0.7")
    monkeypatch.setenv("MENLO_CREDENTIAL", "env-cred")
    robot = Robot()
    assert robot.config.available_modes() == ("livekit",)
    assert robot.config.only_mode() == "livekit"
    assert not robot.connected


def test_the_default_connection_mode_follows_the_slots_that_are_set():
    both = ConnectionConfig(udp=UdpConfig("h"), livekit=LiveKitConfig("ws://x", "r", "t"))
    assert both.available_modes() == ("udp", "hybrid", "livekit")
    assert both.default_mode() == "hybrid"
    assert ConnectionConfig(udp=UdpConfig("h")).default_mode() == "udp"
    assert ConnectionConfig(livekit=LiveKitConfig("ws://x", "r", "t")).default_mode() == "livekit"
    assert ConnectionConfig(udp=UdpConfig("h"), livekit=None, mode="udp").default_mode() == "udp"
    with pytest.raises(ConnectError, match="no connection set"):
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


def test_menlo_persist_in_the_environment_does_the_same(edge, manager, monkeypatch):
    route_manager_rooms_to(edge, monkeypatch)
    monkeypatch.setenv("MENLO_PERSIST", "1")
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


# ── saved robots carry the whole connection ──────────────────────────────────


def test_a_saved_robot_round_trips_every_field(tmp_path):
    path = tmp_path / "robots.toml"
    RobotStore(path).put(
        StoredRobot(
            "lab",
            manager_url="192.168.22.32",
            credential=CRED,
            room="robot-menlo-0001",
            mode="hybrid",
            udp_host="192.168.22.32",
            limits=Limits(0.3, 0.4, 0.8),
        )
    )
    text = path.read_text()
    assert 'mode = "hybrid"' in text and "[robots.lab.limits]" in text and "vx = 0.3" in text
    lab = RobotStore(path).get("lab")
    assert lab is not None and lab.mode == "hybrid" and lab.udp_host == "192.168.22.32"
    assert lab.manager_url == "http://192.168.22.32" and lab.limits == Limits(0.3, 0.4, 0.8)
    assert lab.missing() == ()


def test_a_limits_table_may_set_only_some_speeds(tmp_path):
    path = tmp_path / "robots.toml"
    path.write_text('[robots.lab]\nudp_host = "h"\n[robots.lab.limits]\nvx = 0.3\n')
    lab = RobotStore(path).get("lab")
    assert lab is not None and lab.limits == Limits(vx=0.3)
    assert lab.resolved_mode() == "udp", "only the udp fields are set"


def test_connect_with_no_mode_uses_the_saved_mode_and_limits(monkeypatch):
    RobotStore().put(
        StoredRobot(
            "lab",
            manager_url="http://10.0.0.7",
            credential=CRED,
            mode="udp",
            udp_host="10.0.0.7",
            limits=Limits(0.2, 0.2, 0.4),
        )
    )
    robot = Robot()
    assert robot.config.name == "lab" and robot.config.default_mode() == "udp"
    assert robot.config.available_modes() == ("udp", "hybrid", "livekit")
    assert robot.limits == Limits(0.2, 0.2, 0.4)
    monkeypatch.setenv("MENLO_MODE", "livekit")
    assert Robot().config.default_mode() == "livekit"
    monkeypatch.setenv("MENLO_MODE", "wifi")
    with pytest.raises(ValueError, match="MENLO_MODE='wifi' is not a connection mode"):
        Robot()


def test_a_missing_field_is_a_connect_error_naming_it_and_the_fix(manager):
    RobotStore().put(StoredRobot("lab", manager_url=manager.url, credential=CRED, mode="hybrid"))
    with pytest.raises(ConnectError) as info:
        Robot().connect(timeout=0.5)
    assert "udp_host" in str(info.value)
    assert "menlo robots add lab --udp <robot address>" in str(info.value)
    assert manager.requests == [], "the check comes before any network"

    RobotStore().put(StoredRobot("udp-only", mode="udp", udp_host="10.0.0.9"), default=True)
    with pytest.raises(ConnectError) as info:
        Robot().connect("livekit")
    message = str(info.value)
    assert "manager_url or credential" in message
    assert "menlo robots add udp-only --manager <Asimov Manager URL> --credential" in message
    assert StoredRobot("x", mode="hybrid", udp_host="h").fix_command() == (
        "menlo robots add x --manager <Asimov Manager URL> --credential <SDK credential>"
    )


def test_the_environment_can_name_a_udp_robot(monkeypatch):
    monkeypatch.setenv("MENLO_UDP_HOST", "10.0.0.5")
    cfg = resolve_connection()
    assert cfg.udp is not None and cfg.udp.host == "10.0.0.5" and cfg.default_mode() == "udp"
    monkeypatch.setenv("MENLO_MODE", "hybrid")
    with pytest.raises(ConnectError, match="set MENLO_MANAGER_URL=http://<robot> and"):
        Robot().connect()


def test_an_explicit_robot_name_skips_the_environment(monkeypatch):
    RobotStore().put(StoredRobot("a", "http://a", "ca"))
    RobotStore().put(StoredRobot("b", mode="udp", udp_host="10.0.0.2"))
    monkeypatch.setenv("MENLO_UDP_HOST", "10.0.0.5")
    cfg = ConnectionConfig.from_environment(robot="b")
    assert cfg.udp is not None and cfg.udp.host == "10.0.0.2" and cfg.name == "b"
    with pytest.raises(ConnectError, match="--robot zzz names no saved robot"):
        ConnectionConfig.from_environment(robot="zzz")


def test_persist_keeps_what_the_entry_had(edge, manager, monkeypatch):
    route_manager_rooms_to(edge, monkeypatch)
    RobotStore().put(
        StoredRobot(
            "menlo-0042",
            manager_url=manager.url,
            credential="old",
            mode="hybrid",
            udp_host="10.0.0.2",
            limits=Limits(0.1, 0.1, 0.1),
        )
    )
    cfg = ConnectionConfig(livekit=ManagerConfig(url=manager.url, credential=CRED))
    with Robot(cfg).connect("livekit", timeout=3.0, persist=True):
        pass
    saved = RobotStore().get("menlo-0042")
    assert saved is not None and saved.credential == CRED and saved.room == "robot-menlo-0042"
    assert saved.udp_host == "10.0.0.2" and saved.limits == Limits(0.1, 0.1, 0.1)
    assert saved.mode == "livekit", "the mode the connect used"
