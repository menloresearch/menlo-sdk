"""``menlo login / robots / use / logout`` against the fake manager and a temporary store."""

from __future__ import annotations

import pytest

from menlo.asimov import RobotStore, StoredRobot
from menlo.cli import main

CRED = "eyJpZCI6ImEwY2QxNmRiIn0.secret"


def test_login_validates_against_the_manager_then_saves_without_echoing(manager, capsys):
    assert main(["login", manager.url, "--credential", CRED]) == 0
    out = capsys.readouterr()
    assert "saved menlo-0042 (default)" in out.out and "robot-menlo-0042" in out.out
    assert CRED not in out.out and CRED not in out.err
    assert manager.requests[0][0]["Authorization"] == f"Bearer {CRED}"
    saved = RobotStore().get("menlo-0042")
    assert saved is not None and saved.credential == CRED and saved.room == "robot-menlo-0042"


def test_login_with_a_refused_credential_leaves_the_store_alone(manager, capsys):
    RobotStore().put(StoredRobot("menlo-0042", manager.url, "good", room="robot-menlo-0042"))
    manager.status = 401
    assert main(["login", manager.url, "--credential", "bad"]) == 1
    err = capsys.readouterr().err
    assert "menlo login:" in err and "HTTP 401" in err and "bad" not in err.split("HTTP")[0]
    kept = RobotStore().get("menlo-0042")
    assert kept is not None and kept.credential == "good"


def test_login_reads_a_piped_credential_and_honours_name_and_no_default(manager, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(CRED + "\n"))
    assert main(["login", manager.url, "--name", "rack", "--no-default"]) == 0
    store = RobotStore()
    assert store.get("rack") is not None and store.default is None
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert main(["login", manager.url]) == 1


def test_robots_use_and_logout(capsys):
    assert main(["robots"]) == 0
    assert "no robots saved" in capsys.readouterr().out
    store = RobotStore()
    store.put(StoredRobot("a", "http://a", "ca", room="robot-a"))
    store.put(StoredRobot("b", "http://b:8080", "cb"))
    assert main(["robots"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("* a ") and "http://a" in lines[0] and "robot-a" in lines[0]
    assert lines[1].startswith("  b ") and lines[1].rstrip().endswith("-")
    assert "ca" not in "".join(lines) or "cb" not in "".join(lines)

    assert main(["use", "b"]) == 0
    assert RobotStore().default == "b"
    assert main(["use", "zzz"]) == 1
    assert "no robot named 'zzz'" in capsys.readouterr().err

    assert main(["logout", "b"]) == 0
    reloaded = RobotStore()
    assert "b" not in reloaded and reloaded.default is None
    assert main(["logout", "b"]) == 1


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "login" in capsys.readouterr().out
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
