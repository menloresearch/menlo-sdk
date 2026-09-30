"""How ``menlo`` draws and asks: a compact, inline style (a gutter on the left, one line
per step), with ``rich`` for output and ``questionary`` for questions. Nothing here takes
over the screen; everything scrolls like ordinary output."""

from __future__ import annotations

import contextlib
import os
import select
import sys
from collections.abc import Callable, Iterator, Sequence

import questionary
from rich.console import Console

console = Console(highlight=False)
errors = Console(stderr=True, highlight=False)

_STYLE = questionary.Style(
    [
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("answer", "fg:ansigreen"),
        ("pointer", "fg:ansicyan bold"),
        ("highlighted", "fg:ansicyan"),
        ("instruction", "fg:ansibrightblack"),
    ]
)
_QMARK = "◆"


def interactive() -> bool:
    """Can ``menlo`` ask questions here? Only with a terminal on both ends."""
    return sys.stdin.isatty() and sys.stdout.isatty()


# ── output ───────────────────────────────────────────────────────────────────
def intro(title: str) -> None:
    console.print(f"[dim]┌[/]  [bold]{title}[/]")


def line(text: str = "") -> None:
    console.print(f"[dim]│[/]  {text}" if text else "[dim]│[/]")


def step(title: str) -> None:
    console.print(f"[cyan]◇[/]  [bold]{title}[/]")


def ok(text: str) -> None:
    line(f"[green]✓[/] {text}")


def warn(text: str) -> None:
    line(f"[yellow]![/] {text}")


def fail(text: str) -> None:
    line(f"[red]✗[/] {text}")


def outro(text: str) -> None:
    console.print(f"[dim]└[/]  {text}")


# ── questions ────────────────────────────────────────────────────────────────
class Prompts:
    """:class:`menlo.cli._common.Asker` on the terminal. Ctrl-C raises
    ``KeyboardInterrupt``, which ``menlo`` turns into exit code 130."""

    def text(
        self,
        message: str,
        *,
        default: str = "",
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        def check(answer: str) -> bool | str:
            problem = validate(answer.strip()) if validate is not None else None
            return True if problem is None else problem

        answer = questionary.text(
            message, default=default, validate=check, qmark=_QMARK, style=_STYLE
        ).unsafe_ask()
        return str(answer).strip()

    def select(self, message: str, choices: Sequence[tuple[str, str]], *, default: str) -> str:
        options = [
            questionary.Choice(value, value=value, description=about) for value, about in choices
        ]
        chosen = next((c for c in options if c.value == default), None)
        answer = questionary.select(
            message, choices=options, default=chosen, qmark=_QMARK, style=_STYLE
        ).unsafe_ask()
        return str(answer)

    def secret(self, message: str, *, keep: bool = False) -> str:
        hint = " (Enter keeps the saved one)" if keep else ""

        def check(answer: str) -> bool | str:
            return True if (keep or answer.strip()) else "an SDK credential is required"

        answer = questionary.password(
            message + hint, validate=check, qmark=_QMARK, style=_STYLE
        ).unsafe_ask()
        return str(answer).strip()

    def confirm(self, message: str, *, default: bool) -> bool:
        return bool(
            questionary.confirm(message, default=default, qmark=_QMARK, style=_STYLE).unsafe_ask()
        )


def confirm(question: str) -> bool:
    """Ask ``question [y/N]`` on one line. Only ``y`` or ``yes`` is yes; Enter, anything
    else, or the end of input is no."""
    try:
        answer = console.input(f"[bold]{question}[/] \\[y/N] ")
    except EOFError:
        console.print()
        return False
    return answer.strip().lower() in ("y", "yes")


# ── keys while something runs ────────────────────────────────────────────────
@contextlib.contextmanager
def keypresses() -> Iterator[Callable[[], str | None]]:
    """Read single keys without waiting for Enter, for "press q to quit". Yields a function
    that returns the key pressed since the last call, or ``None``. Off a terminal it always
    returns ``None``."""
    if not sys.stdin.isatty() or os.name != "posix":
        yield lambda: None
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    def pressed() -> str | None:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return sys.stdin.read(1) if ready else None

    try:
        tty.setcbreak(fd)
        yield pressed
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
