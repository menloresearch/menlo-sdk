"""Saved robots, and how ``Robot()`` finds one with no config.

    Robot().connect()            # the saved default robot, on its saved connection mode

The lookup, in order. The first that yields a connection wins; the connection fields of
the environment and of a saved robot are never mixed:

1. The environment: ``MENLO_UDP_HOST`` (the robot's address for the ``udp`` and ``hybrid``
   connection modes) and/or ``MENLO_MANAGER_URL`` + ``MENLO_CREDENTIAL`` (Asimov Manager and
   an SDK credential, for ``hybrid`` and ``livekit``).
2. ``~/.menlo/robots.toml`` (``$MENLO_HOME/robots.toml``): the robot named by
   ``MENLO_ROBOT``, else the file's ``default``, else the only robot in it.
3. Nothing: :class:`ConnectError` naming both, and ``menlo setup``. The SDK does not
   probe the network for a robot.

``MENLO_MODE`` overrides the connection mode ``connect()`` uses when given none, and
``MENLO_LIMITS`` the velocity clamp, whichever of the two supplied the connection.

A saved robot holds everything one connection mode needs::

    default = "lab"

    [robots.lab]
    mode = "hybrid"                       # udp | hybrid | livekit
    udp_host = "192.168.22.32"            # udp and hybrid
    manager_url = "http://192.168.22.32"  # hybrid and livekit
    credential = "..."                    # hybrid and livekit
    room = "robot-menlo-0001"             # what Asimov Manager answered last

    [robots.lab.limits]                   # optional; defaults are the firmware caps
    vx = 0.3
    vy = 0.4
    vyaw = 0.8

The file holds SDK credentials, so it is the user's alone: the directory is 0700, the file
0600, and it is written whole through a temporary file. ``menlo setup``, ``menlo robots
add``, ``Robot.connect(persist=True)`` and ``MENLO_PERSIST=1`` write it; the last two only
after a connect that succeeded, so a credential that did not work never replaces one that
did.
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
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from menlo.asimov._command import Limits
from menlo.asimov._errors import ConnectError
from menlo.asimov.connection import (
    MODES,
    ConnectionConfig,
    ConnectMode,
    ManagerConfig,
    UdpConfig,
)

log = logging.getLogger(__name__)

ENV_HOME = "MENLO_HOME"
ENV_ROBOT = "MENLO_ROBOT"
ENV_MODE = "MENLO_MODE"
ENV_UDP_HOST = "MENLO_UDP_HOST"
ENV_MANAGER_URL = "MENLO_MANAGER_URL"
ENV_CREDENTIAL = "MENLO_CREDENTIAL"
ENV_PERSIST = "MENLO_PERSIST"

STORE_FILE = "robots.toml"
_TRUE = frozenset({"1", "true", "yes", "on"})
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

#: The saved fields each connection mode needs.
REQUIRED: dict[str, tuple[str, ...]] = {
    "udp": ("udp_host",),
    "hybrid": ("udp_host", "manager_url", "credential"),
    "livekit": ("manager_url", "credential"),
}
#: The ``menlo robots add`` flag that sets each field.
FLAGS = {
    "udp_host": "--udp <robot address>",
    "manager_url": "--manager <Asimov Manager URL>",
    "credential": "--credential <SDK credential>",
}


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


def env_mode(environ: Mapping[str, str] | None = None) -> ConnectMode | None:
    """``MENLO_MODE``, checked. ``ValueError`` for a value that is not a connection mode."""
    env = os.environ if environ is None else environ
    value = env.get(ENV_MODE, "").strip().lower()
    if not value:
        return None
    if value not in MODES:
        raise ValueError(
            f"{ENV_MODE}={value!r} is not a connection mode; one of {', '.join(MODES)}"
        )
    return value


@dataclass(frozen=True)
class StoredRobot:
    """One saved robot: how to reach it, on which connection mode, and its limits.

    Only the fields its connection mode needs have to be set (see :data:`REQUIRED`);
    :meth:`missing` names the ones that are not. ``room`` is what Asimov Manager answered
    the last time the credential worked (it names the body: ``robot-<serial>``)."""

    name: str
    manager_url: str | None = None
    credential: str | None = field(default=None, repr=False)  # a bearer secret
    room: str | None = None
    mode: ConnectMode | None = None
    udp_host: str | None = None
    limits: Limits | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a saved robot needs a name")
        if self.mode is not None and self.mode not in MODES:
            raise ValueError(
                f"saved robot {self.name!r}: unknown mode {self.mode!r}; one of {', '.join(MODES)}"
            )
        if self.credential is not None and not self.credential.strip():
            raise ValueError(f"saved robot {self.name!r} has an empty credential")
        for name in ("udp_host", "manager_url", "room"):
            value = getattr(self, name)
            if value is not None:
                value = value.strip() or None
                object.__setattr__(self, name, value)
        if self.manager_url is not None:
            # Normalise the URL the way ManagerConfig will read it back, so the file holds
            # what a connect uses and two spellings of one manager do not become two entries.
            object.__setattr__(
                self, "manager_url", ManagerConfig(url=self.manager_url, credential="-").url
            )

    def resolved_mode(self) -> ConnectMode | None:
        """``mode``, else the one mode the set fields allow, else ``None``."""
        if self.mode is not None:
            return self.mode
        possible = [m for m in MODES if not self.missing(m)]
        return possible[0] if len(possible) == 1 else None

    def missing(self, mode: ConnectMode | None = None) -> tuple[str, ...]:
        """The fields ``mode`` (default: this robot's) needs and this entry lacks."""
        mode = mode if mode is not None else self.mode
        if mode is None:
            return () if self.udp_host or self.manager_url else ("mode",)
        return tuple(f for f in REQUIRED[mode] if not getattr(self, f))

    def fix_command(self, mode: ConnectMode | None = None) -> str:
        """The ``menlo robots add`` command that fills what :meth:`missing` names."""
        flags = [FLAGS.get(f, f"--{f}") for f in self.missing(mode)]
        if mode is not None and mode != self.mode:
            flags.insert(0, f"--mode {mode}")
        return f"menlo robots add {self.name} " + " ".join(flags)

    def manager(self, *, label: str | None = None) -> ManagerConfig:
        if not self.manager_url or not self.credential:
            raise ConnectError(
                f"saved robot {self.name!r} has no Asimov Manager URL and credential: "
                f"`{self.fix_command('livekit')}`"
            )
        return ManagerConfig(url=self.manager_url, credential=self.credential, label=label)

    def connection(self, *, label: str | None = None) -> ConnectionConfig:
        """The config this robot describes: every connection its fields allow, its saved
        mode and limits. A mode whose fields are missing fails at ``connect()`` with a
        :class:`ConnectError` naming them and the command that sets them."""
        hints = {
            "udp": f"saved robot {self.name!r} has no udp_host: "
            f"`menlo robots add {self.name} {FLAGS['udp_host']}`",
            "livekit": f"saved robot {self.name!r} has no "
            + " or ".join(f for f in ("manager_url", "credential") if not getattr(self, f))
            + f": `menlo robots add {self.name} "
            + " ".join(FLAGS[f] for f in ("manager_url", "credential") if not getattr(self, f))
            + "`",
        }
        return ConnectionConfig(
            udp=UdpConfig(self.udp_host) if self.udp_host else None,
            livekit=self.manager(label=label) if self.manager_url and self.credential else None,
            limits=self.limits,
            mode=self.mode,
            name=self.name,
            hints=hints,
        )


def robot_name(manager_url: str, room: str | None) -> str:
    """The key an entry is saved under when nobody named it: the serial the room carries
    (``robot-menlo-0001`` -> ``menlo-0001``), else the manager's host."""
    if room:
        return room.removeprefix("robot-") or room
    url = manager_url if "://" in manager_url else "http://" + manager_url
    return urlsplit(url).hostname or manager_url


def _limits_from(table: Any, where: str) -> Limits:
    if not isinstance(table, dict):
        raise ValueError(f"{where} must be a table")
    unknown = set(table) - {f.name for f in fields(Limits)}
    if unknown:
        raise ValueError(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    try:
        return Limits(**{k: float(v) for k, v in table.items()})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: {exc}") from exc


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
            where = f"{self.path}: [robots.{name}]"
            if not isinstance(entry, dict):
                raise ValueError(f"{where} must be a table")

            def text(key: str, entry: dict[str, Any] = entry) -> str | None:
                value = entry.get(key)
                return str(value) if value not in (None, "") else None

            try:
                robot = StoredRobot(
                    name=name,
                    manager_url=text("manager_url"),
                    credential=text("credential"),
                    room=text("room"),
                    mode=text("mode"),  # type: ignore[arg-type]
                    udp_host=text("udp_host"),
                    limits=_limits_from(entry["limits"], f"{where}.limits")
                    if "limits" in entry
                    else None,
                )
            except ValueError as exc:
                raise ValueError(f"{where} is invalid: {exc}") from exc
            if robot.missing() == ("mode",):
                raise ValueError(f"{where} is incomplete: it has neither udp_host nor manager_url")
            self.robots[name] = robot
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
        robot: a lookup that finds nothing is an answer, not an error."""
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

        A name taken from the ROOM Asimov Manager answered is the manager's choice. So an
        entry is only replaced by one for the same manager unless ``allow_manager_change``
        says the caller chose the name: otherwise a misconfigured (or hostile) manager
        answering another robot's room would silently take over that robot's saved URL and
        credential."""
        previous = self.robots.get(robot.name)
        if (
            previous is not None
            and previous.manager_url is not None
            and robot.manager_url is not None
            and previous.manager_url != robot.manager_url
            and not allow_manager_change
        ):
            raise ValueError(
                f"{robot.name!r} is already saved for Asimov Manager {previous.manager_url}, "
                f"but this one answered from {robot.manager_url}. Save it under another name, "
                f"or remove the old entry first (`menlo robots remove {robot.name}`)."
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
            "# menlo-sdk: saved robots. Written by `menlo setup`, `menlo robots add` and",
            "# Robot.connect(persist=True); holds SDK credentials, keep it 0600.",
        ]
        if self.default is not None:
            lines.append(f"default = {_toml_str(self.default)}")
        for name in sorted(self.robots):
            r = self.robots[name]
            lines += ["", f"[robots.{_toml_key(name)}]"]
            for key in ("mode", "udp_host", "manager_url", "credential", "room"):
                value = getattr(r, key)
                if value:
                    lines.append(f"{key} = {_toml_str(value)}")
            if r.limits is not None:
                lines += [
                    "",
                    f"[robots.{_toml_key(name)}.limits]",
                    f"vx = {r.limits.vx!r}",
                    f"vy = {r.limits.vy!r}",
                    f"vyaw = {r.limits.vyaw!r}",
                ]
        return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's, so ``json.dumps`` is
    the quoting needed, except DEL (U+007F), which JSON leaves raw and TOML forbids in a
    basic string. One such byte in a room name would make the whole file unreadable."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _toml_str(name)


# ── the lookup ───────────────────────────────────────────────────────────────


def resolve_connection(
    environ: Mapping[str, str] | None = None,
    *,
    store: RobotStore | None = None,
    robot: str | None = None,
) -> ConnectionConfig:
    """The config ``Robot()`` uses when given none. See the module docstring for the
    order. ``robot`` names a saved robot and skips the environment's connection fields.
    Raises :class:`ConnectError` naming every way to configure one when nothing does."""
    env = os.environ if environ is None else environ
    mode = env_mode(env)
    if robot is None:
        from_env = _from_environment(env, mode)
        if from_env is not None:
            return from_env
    if store is None:
        store = RobotStore(environ=env)
    wanted = robot or env.get(ENV_ROBOT, "").strip() or None
    found = store.get(wanted)
    if found is not None:
        config = found.connection()
        return replace(config, mode=mode) if mode is not None else config
    if wanted is not None:
        where = f"--robot {wanted}" if robot else f"{ENV_ROBOT}={wanted!r}"
        raise ConnectError(
            f"{where} names no saved robot in {store.path}; it has: {store.names()}. "
            f"`menlo robots` lists them, `menlo robots add {wanted}` adds one"
        )
    if len(store) > 1:
        raise ConnectError(
            f"{store.path} has {len(store)} robots and no default: `menlo robots use <name>` "
            f"picks one, or set {ENV_ROBOT}=<name>. Have: {store.names()}"
        )
    raise ConnectError(
        "no robot is configured. Run `menlo setup` to save one, pass a ConnectionConfig to "
        f"Robot(cfg), or set {ENV_UDP_HOST}=<robot address> and/or {ENV_MANAGER_URL}="
        f"http://<robot> with {ENV_CREDENTIAL}=<SDK credential> in the environment. Saved "
        f"robots are kept in {store.path}."
    )


def _from_environment(env: Mapping[str, str], mode: ConnectMode | None) -> ConnectionConfig | None:
    host = env.get(ENV_UDP_HOST, "").strip()
    url = env.get(ENV_MANAGER_URL, "").strip()
    credential = env.get(ENV_CREDENTIAL, "").strip()
    if bool(url) != bool(credential):
        have, need = (ENV_MANAGER_URL, ENV_CREDENTIAL) if url else (ENV_CREDENTIAL, ENV_MANAGER_URL)
        raise ConnectError(
            f"{have} is set but {need} is not; the environment needs both "
            f"({ENV_MANAGER_URL}=http://<robot> {ENV_CREDENTIAL}=<SDK credential>)"
        )
    if not host and not url:
        return None
    return ConnectionConfig(
        udp=UdpConfig(host) if host else None,
        livekit=ManagerConfig(url=url, credential=credential) if url else None,
        mode=mode,
        hints={
            "udp": f"set {ENV_UDP_HOST}=<robot address>",
            "livekit": (
                f"set {ENV_MANAGER_URL}=http://<robot> and {ENV_CREDENTIAL}=<SDK credential>"
            ),
        },
    )


def persist(
    config: ConnectionConfig,
    room: str | None,
    *,
    name: str | None = None,
    mode: ConnectMode | None = None,
) -> StoredRobot:
    """Record the connection a connect just succeeded with. The entry is keyed by
    ``name``, else by the config's saved name, else by the serial in ``room``, else by
    Asimov Manager's host; it keeps what an existing entry of that name had and the connect
    did not use, and becomes the default when the store had none."""
    manager = config.livekit
    if not isinstance(manager, ManagerConfig):
        raise ValueError(
            "persist needs a ManagerConfig in the livekit slot: a saved robot is found by "
            "its Asimov Manager, and this config has no manager"
        )
    store = RobotStore()
    key = name or config.name or robot_name(manager.url, room)
    previous = store.robots.get(key) or StoredRobot(key, manager_url=manager.url)
    entry = replace(
        previous,
        manager_url=manager.url,
        credential=manager.credential,
        room=room or previous.room,
        mode=mode or previous.mode,
        udp_host=config.udp.host if config.udp is not None else previous.udp_host,
        limits=config.limits if config.limits is not None else previous.limits,
    )
    return store.put(entry, allow_manager_change=name is not None or config.name is not None)


__all__ = [
    "ENV_CREDENTIAL",
    "ENV_HOME",
    "ENV_MANAGER_URL",
    "ENV_MODE",
    "ENV_PERSIST",
    "ENV_ROBOT",
    "ENV_UDP_HOST",
    "FLAGS",
    "REQUIRED",
    "RobotStore",
    "StoredRobot",
    "env_mode",
    "home",
    "persist",
    "resolve_connection",
    "robot_name",
    "store_path",
]
