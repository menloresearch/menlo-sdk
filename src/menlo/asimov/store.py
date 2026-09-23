"""What the SDK remembers about robots, and how ``Robot()`` finds one with no config.

    Robot().connect()            # the robot from the environment, or the store's default

The lookup, in order — the first that yields a config wins, nothing is merged:

1. ``MENLO_MANAGER_URL`` + ``MENLO_CREDENTIAL`` in the environment.
2. ``~/.menlo/robots.toml`` (``$MENLO_HOME/robots.toml``): the robot named by
   ``MENLO_ROBOT``, else the store's ``default``, else the only robot in it.
3. Nothing: :class:`ConnectError` naming those two, and ``menlo login``. The SDK does not
   probe the LAN — a name like ``asimov.local`` says where a robot is, not who may drive it.

The store holds an SDK credential per robot, so it is the user's alone: the directory is
0700, the file 0600, and it is written whole through a temporary file. ``menlo login``,
``Robot.connect(persist=True)`` and ``MENLO_PERSIST=1`` write it — the last two only after
a connect that succeeded, so a credential that did not work never replaces one that did.

::

    default = "menlo-0001"

    [robots.menlo-0001]
    manager_url = "http://192.168.22.32"
    credential = "…"
    room = "robot-menlo-0001"
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import stat
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from menlo.asimov._errors import ConnectError
from menlo.asimov.connection import ConnectionConfig, ManagerConfig

log = logging.getLogger(__name__)

ENV_HOME = "MENLO_HOME"
ENV_MANAGER_URL = "MENLO_MANAGER_URL"
ENV_CREDENTIAL = "MENLO_CREDENTIAL"
ENV_ROBOT = "MENLO_ROBOT"
ENV_PERSIST = "MENLO_PERSIST"

STORE_FILE = "robots.toml"
_TRUE = frozenset({"1", "true", "yes", "on"})
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def home(environ: Mapping[str, str] | None = None) -> Path:
    """``$MENLO_HOME``, else ``~/.menlo``."""
    env = os.environ if environ is None else environ
    override = env.get(ENV_HOME, "").strip()
    return Path(override).expanduser() if override else Path.home() / ".menlo"


def store_path(environ: Mapping[str, str] | None = None) -> Path:
    return home(environ) / STORE_FILE


def env_flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(name, "").strip().lower() in _TRUE


@dataclass(frozen=True)
class StoredRobot:
    """One robot the store knows: where its manager is and the credential that drives it.
    ``room`` is what the manager answered the last time this credential worked (it names
    the body: ``robot-<serial>``); ``None`` for an entry that never connected."""

    name: str
    manager_url: str
    credential: str = field(repr=False)  # a bearer secret; keep it out of logs and tracebacks
    room: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a stored robot needs a name")
        if not self.credential:
            raise ValueError(f"stored robot {self.name!r} has no credential")
        # Normalise the URL the way ManagerConfig will read it back, so the file holds
        # what a connect uses and two spellings of one manager do not become two entries.
        object.__setattr__(
            self, "manager_url", ManagerConfig(url=self.manager_url, credential=self.credential).url
        )

    def manager(self, *, label: str | None = None) -> ManagerConfig:
        return ManagerConfig(url=self.manager_url, credential=self.credential, label=label)

    def connection(self, *, label: str | None = None) -> ConnectionConfig:
        """A config that connects on ``"livekit"`` — the one lane a manager URL and a
        credential describe."""
        return ConnectionConfig(livekit=self.manager(label=label))


def robot_name(manager_url: str, room: str | None) -> str:
    """The key an entry is stored under when nobody named it: the serial the room carries
    (``robot-menlo-0001`` -> ``menlo-0001``), else the manager's host."""
    if room:
        return room.removeprefix("robot-") or room
    url = manager_url if "://" in manager_url else "http://" + manager_url
    return urlsplit(url).hostname or manager_url


class RobotStore:
    """``robots.toml``, loaded on construction. Every mutation saves at once."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else store_path(environ)
        self.robots: dict[str, StoredRobot] = {}
        self.default: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = self.path.read_bytes()
            mode = stat.S_IMODE(self.path.stat().st_mode)
        except FileNotFoundError:
            return
        if mode & 0o077:
            log.warning("%s is mode %o; it holds credentials and should be 0600", self.path, mode)
        try:
            data = tomllib.loads(raw.decode())
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{self.path} is not a valid robots.toml: {exc}") from exc
        robots = data.get("robots", {})
        if not isinstance(robots, dict):
            raise ValueError(f"{self.path}: [robots] must be a table")
        for name, entry in robots.items():
            if not isinstance(entry, dict):
                raise ValueError(f"{self.path}: [robots.{name}] must be a table")
            try:
                self.robots[name] = StoredRobot(
                    name=name,
                    manager_url=str(entry["manager_url"]),
                    credential=str(entry["credential"]),
                    room=str(entry["room"]) if entry.get("room") else None,
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{self.path}: [robots.{name}] is incomplete: {exc}") from exc
        default = data.get("default")
        if default is not None and default not in self.robots:
            log.warning("%s: default %r names no robot in the file", self.path, default)
            default = None
        self.default = str(default) if default is not None else None

    # ── reading ──────────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self.robots)

    def __iter__(self) -> Iterator[StoredRobot]:
        return iter(self.robots.values())

    def __contains__(self, name: object) -> bool:
        return name in self.robots

    def get(self, name: str | None = None) -> StoredRobot | None:
        """The robot called ``name``; with ``None``, the default, or the only robot when
        there is exactly one and no default was chosen. ``None`` when there is no such
        robot — a lookup that finds nothing is an answer, not an error."""
        if name is not None:
            return self.robots.get(name)
        if self.default is not None:
            return self.robots.get(self.default)
        if len(self.robots) == 1:
            return next(iter(self.robots.values()))
        return None

    # ── writing: every change is saved at once ───────────────────────────────
    def put(
        self,
        robot: StoredRobot,
        *,
        default: bool | None = None,
        allow_manager_change: bool = False,
    ) -> StoredRobot:
        """Add or replace ``robot`` by name. ``default=None`` makes it the default only when
        the store had none; ``True`` always, ``False`` never.

        The name usually comes from the ROOM the manager answered, and a manager decides
        what it answers. So an entry is only replaced by one for the same manager unless
        ``allow_manager_change`` says the caller chose the name deliberately: otherwise a
        misconfigured (or hostile) manager answering another robot's room would silently
        take over that robot's saved URL and credential."""
        previous = self.robots.get(robot.name)
        if (
            previous is not None
            and previous.manager_url != robot.manager_url
            and not allow_manager_change
        ):
            raise ValueError(
                f"{robot.name!r} is already saved for manager {previous.manager_url}, but this "
                f"one answered from {robot.manager_url}. Save it under another name "
                f"(`menlo login {robot.manager_url} --name <name>`), or forget the old entry "
                f"first (`menlo logout {robot.name}`)."
            )
        self.robots[robot.name] = robot
        if default or (default is None and self.default is None):
            self.default = robot.name
        self.save()
        return robot

    def remove(self, name: str) -> StoredRobot | None:
        gone = self.robots.pop(name, None)
        if gone is None:
            return None
        if self.default == name:
            self.default = None
        self.save()
        return gone

    def use(self, name: str) -> StoredRobot:
        """Make ``name`` the default. ``KeyError`` for a name the store does not have."""
        if name not in self.robots:
            raise KeyError(f"no robot named {name!r} in {self.path}; have: {self.names()}")
        self.default = name
        self.save()
        return self.robots[name]

    def names(self) -> str:
        return ", ".join(sorted(self.robots)) or "(none)"

    def save(self) -> None:
        """Write the whole file, 0600, through a temporary file in the same directory so a
        reader never sees a half-written store."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._dump())
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def _dump(self) -> str:
        lines = [
            "# menlo-sdk: robots this machine may drive. Written by `menlo login` and",
            "# Robot.connect(persist=True); holds SDK credentials, keep it 0600.",
        ]
        if self.default is not None:
            lines.append(f"default = {_toml_str(self.default)}")
        for name in sorted(self.robots):
            r = self.robots[name]
            lines += [
                "",
                f"[robots.{_toml_key(name)}]",
                f"manager_url = {_toml_str(r.manager_url)}",
                f"credential = {_toml_str(r.credential)}",
            ]
            if r.room:
                lines.append(f"room = {_toml_str(r.room)}")
        return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's, so ``json.dumps`` is
    the quoting needed — except DEL (U+007F), which JSON leaves raw and TOML forbids in a
    basic string. One such byte in a room name would make the whole file unreadable."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _toml_str(name)


# ── the lookup ───────────────────────────────────────────────────────────────


def resolve_connection(
    environ: Mapping[str, str] | None = None, *, store: RobotStore | None = None
) -> ConnectionConfig:
    """The config ``Robot()`` uses when given none. See the module docstring for the
    order; raises :class:`ConnectError` naming every way to configure one when nothing
    does."""
    env = os.environ if environ is None else environ
    url = env.get(ENV_MANAGER_URL, "").strip()
    credential = env.get(ENV_CREDENTIAL, "").strip()
    if url and credential:
        return ConnectionConfig(livekit=ManagerConfig(url=url, credential=credential))
    if url or credential:
        have, need = (ENV_MANAGER_URL, ENV_CREDENTIAL) if url else (ENV_CREDENTIAL, ENV_MANAGER_URL)
        raise ConnectError(
            f"{have} is set but {need} is not; the environment needs both "
            f"({ENV_MANAGER_URL}=http://<robot> {ENV_CREDENTIAL}=<from `asimovctl sdk-token "
            "create` on the robot>)"
        )
    if store is None:
        store = RobotStore(environ=env)
    wanted = env.get(ENV_ROBOT, "").strip() or None
    found = store.get(wanted)
    if found is not None:
        return found.connection()
    if wanted is not None:
        raise ConnectError(
            f"{ENV_ROBOT}={wanted!r} names no robot in {store.path}; it has: {store.names()}. "
            "`menlo robots` lists them, `menlo login <manager-url>` adds one"
        )
    if len(store) > 1:
        raise ConnectError(
            f"{store.path} has {len(store)} robots and no default: `menlo use <name>` picks "
            f"one, or set {ENV_ROBOT}=<name>. Have: {store.names()}"
        )
    raise ConnectError(
        "no robot is configured. Three ways: pass a ConnectionConfig to Robot(cfg); set "
        f"{ENV_MANAGER_URL}=http://<robot> and {ENV_CREDENTIAL}=<credential> in the "
        f"environment; or save one with `menlo login http://<robot> --credential <credential>` "
        f"(kept in {store.path}). A credential comes from `asimovctl sdk-token create "
        "--role control` on the robot. The SDK does not probe the LAN for asimov.local."
    )


def persist(config: ConnectionConfig, room: str | None, *, name: str | None = None) -> StoredRobot:
    """Record the manager URL and credential a connect just succeeded with. The entry is
    keyed by ``name``, else by the serial in ``room``, else by the manager's host, and
    becomes the default when the store had none."""
    manager = config.livekit
    if not isinstance(manager, ManagerConfig):
        raise ValueError(
            "persist needs a ManagerConfig in the livekit slot: the store keeps a manager "
            "URL and a credential, and this config has no manager"
        )
    store = RobotStore()
    entry = StoredRobot(
        name=name or robot_name(manager.url, room),
        manager_url=manager.url,
        credential=manager.credential,
        room=room,
    )
    previous = store.robots.get(entry.name)
    if previous is not None and previous.room and not room:
        entry = replace(entry, room=previous.room)
    return store.put(entry, allow_manager_change=name is not None)


__all__ = [
    "ENV_CREDENTIAL",
    "ENV_HOME",
    "ENV_MANAGER_URL",
    "ENV_PERSIST",
    "ENV_ROBOT",
    "RobotStore",
    "StoredRobot",
    "home",
    "persist",
    "resolve_connection",
    "robot_name",
    "store_path",
]
