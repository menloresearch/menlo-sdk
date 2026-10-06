"""What every ``menlo`` command shares: exit codes, errors, and connecting to the robot.

Nothing here imports ``rich`` or ``questionary``; the commands that draw import them.
"""

from __future__ import annotations

import argparse
import collections
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Protocol

from menlo.asimov import ConnectionConfig, Robot, State

EXIT_OK = 0
EXIT_ERROR = 1  # a connection, robot or saved-robots error
EXIT_USAGE = 2  # a command line that cannot run as given
EXIT_NOT_READY = 3  # no live state (nothing sent), or the robot did not get there
EXIT_CANCELLED = 4  # you answered no; nothing was sent
EXIT_INTERRUPTED = 130  # Ctrl-C

#: How long a command waits for the robot's first state sample.
CONNECT_TIMEOUT_S = 5.0


class UsageError(Exception):
    """The command line cannot run as given (exit 2)."""


class NotFeasible(Exception):
    """There is no live state to send against (exit 3). The message starts with
    ``Not feasible:`` and says why and what to do."""


def config_for(args: argparse.Namespace) -> ConnectionConfig:
    """The connection a command uses: ``--robot NAME``, else the environment and the
    default saved robot, exactly as ``Robot()`` finds one."""
    return ConnectionConfig.from_environment(robot=args.robot)


@contextmanager
def connected(args: argparse.Namespace, *, require_state: bool = True) -> Iterator[Robot]:
    """``with Robot(config).connect(mode) as robot``: the command's robot, closed on the
    way out whatever happens. Connecting sends nothing to the robot."""
    robot = Robot(config_for(args))
    with robot.connect(args.mode, timeout=CONNECT_TIMEOUT_S, require_state=require_state):
        yield robot


class StateRate:
    """Counts state samples as they arrive, for "how often does the robot report?"."""

    def __init__(self, robot: Robot, window_s: float = 1.0) -> None:
        self._window = window_s
        self._times: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()
        self._started = time.monotonic()
        robot.on_state = self._seen

    def _seen(self, state: State) -> None:
        with self._lock:
            self._times.append(state.received_at)

    @property
    def hz(self) -> float:
        """Samples per second over the last ``window_s`` (or since counting began)."""
        now = time.monotonic()
        with self._lock:
            while self._times and now - self._times[0] > self._window:
                self._times.popleft()
            count = len(self._times)
        span = min(self._window, now - self._started)
        return count / span if span > 0 else 0.0


class Asker(Protocol):
    """The questions a wizard asks. ``menlo.cli._ui.Prompts`` asks them on the terminal;
    a test answers them from a script."""

    def text(
        self,
        message: str,
        *,
        default: str = "",
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        """A typed answer. ``validate`` returns what is wrong with it, or ``None``."""
        ...

    def select(self, message: str, choices: Sequence[tuple[str, str]], *, default: str) -> str:
        """``choices`` are ``(value, description)``; returns the chosen value."""
        ...

    def secret(self, message: str, *, keep: bool = False) -> str:
        """A masked answer. With ``keep``, an empty answer keeps the saved value."""
        ...

    def confirm(self, message: str, *, default: bool) -> bool: ...
