"""Record what a session saw and sent to a JSON-lines file, and read it back.

One object per line: ``{"t": <monotonic s>, "kind": "state", ...}`` for every accepted
sample and ``{"t": ..., "kind": "sent", ...}`` for every command that left. Frames and
audio are not recorded here.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from asimov_sdk._outcome import Sent
from asimov_sdk._state import State

if TYPE_CHECKING:
    from asimov_sdk.robot import Robot


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, tuple | list):
        return [_plain(x) for x in obj]
    if isinstance(obj, enum.Enum):
        return obj.name
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


class Recording:
    """Context manager: ``with robot.record("run.jsonl") as rec: ...``."""

    def __init__(
        self,
        robot: Robot,
        path: str | os.PathLike[str],
        *,
        states: bool = True,
        commands: bool = True,
    ) -> None:
        self._robot = robot
        self.path = Path(path)
        self._states, self._commands = states, commands
        self.samples = 0
        self.commands_written = 0
        self._fh: Any = None
        self._prev_state: Any = None
        self._prev_sent: Any = None

    def _write(self, kind: str, obj: Any) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps({"t": time.monotonic(), "kind": kind, **_plain(obj)}) + "\n")

    def _on_state(self, state: State) -> None:
        self._write("state", state)
        self.samples += 1
        if self._prev_state is not None:
            self._prev_state(state)

    def _on_sent(self, sent: Sent) -> None:
        self._write("sent", {"name": sent.name, "sequence": sent.sequence, "command": sent.command})
        self.commands_written += 1
        if self._prev_sent is not None:
            self._prev_sent(sent)

    def __enter__(self) -> Self:
        self._prev_state, self._prev_sent = self._robot.on_state, self._robot._on_sent
        self._fh = self.path.open("w", encoding="utf-8")
        if self._states:
            self._robot.on_state = self._on_state
        if self._commands:
            self._robot._on_sent = self._on_sent
        return self

    def __exit__(self, *exc: object) -> None:
        if self._states:
            self._robot.on_state = self._prev_state
        if self._commands:
            self._robot._on_sent = self._prev_sent
        fh, self._fh = self._fh, None
        if fh is not None:
            fh.close()


def load(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    """Yield the recorded lines as dicts, in order. ``kind`` is ``"state"`` or ``"sent"``."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
