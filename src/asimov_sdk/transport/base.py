"""The seam between the robot API and a wire.

A :class:`Transport` moves neutral :data:`~asimov_sdk._command.Command` objects to the
robot and neutral :class:`~asimov_sdk._state.State` samples (and, when the edge grows
them, :class:`~asimov_sdk._outcome.Outcome` verdicts) back. It owns encoding, sockets
and threads. It knows nothing about latching, clamping, waits or the error model — those
live in :class:`~asimov_sdk.robot.Robot`, once, for every transport.

Two transports are planned and one exists:

* ``direct`` — :class:`asimov_sdk.transport.udp.UdpTransport`. Bare ``asimov.io``
  protobufs to the edge's ``UdpConnector`` on the robot's LAN. Shipped.
* ``cloud`` — the platform path: a LiveKit room shared with the edge, ``CloudCommand``
  envelopes out, ``EdgeTelemetry`` back. Not in this package yet; it must implement
  exactly this protocol and nothing else changes.

Threading contract: a transport may call the subscribed callbacks from any thread of
its own. Callbacks must be cheap and must not block; ``Robot`` hands the work off.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from asimov_sdk._command import Command
from asimov_sdk._outcome import Applied, Refused
from asimov_sdk._state import State
from asimov_sdk._state import Transport as TransportKind

StateCallback = Callable[[State], None]
OutcomeCallback = Callable[[Applied | Refused], None]
ControllerCallback = Callable[[str | None, str | None, str], None]  # previous, current, reason


@runtime_checkable
class Transport(Protocol):
    """What a wire has to provide. See the module docstring for the contract."""

    kind: TransportKind
    #: Human-readable address of the robot for this wire: ``"host:8850"``, ``"<robot_id>-body"``.
    endpoint: str
    #: How long ``Sent.wait_outcome`` waits by default on this wire. A LAN datagram and a
    #: LiveKit round trip are different animals.
    default_outcome_timeout: float

    def open(self) -> None:
        """Bind, connect, start reader threads. Raise ``ConnectFailed`` on failure."""

    def close(self) -> None:
        """Stop threads, release sockets. Idempotent; never raises."""

    def send(self, command: Command) -> int:
        """Encode and send one command. Returns the sequence number stamped on it.
        Raise ``LinkLost`` when the wire is gone; never block on the robot."""

    def subscribe_state(self, callback: StateCallback) -> None:
        """Every telemetry sample the robot pushes, already normalised to ``State``."""

    def subscribe_outcome(self, callback: OutcomeCallback) -> None:
        """Per-command verdicts, when the edge sends them. A transport whose wire has no
        outcome channel yet simply never calls this."""

    def subscribe_controller_change(self, callback: ControllerCallback) -> None:
        """Who holds the body now. Same caveat as outcomes."""
