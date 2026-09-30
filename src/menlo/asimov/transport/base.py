"""The seam between the robot API and a wire.

A :class:`Transport` moves neutral :data:`~menlo.asimov._command.Command` objects to the
robot and neutral :class:`~menlo.asimov._state.State` samples (and, on a wire that carries
them, :class:`~menlo.asimov._outcome.Outcome` verdicts) back. It owns encoding, sockets
and threads. It knows nothing about latching, clamping, waits or the error model; those
live in :class:`~menlo.asimov.robot.Robot`, once, for every transport.

Three transports ship, one per connection mode: ``UdpTransport`` (``udp``),
``HybridTransport`` (``hybrid``: UDP commands and state, room media) and
``LiveKitTransport`` (``livekit``: everything through the room). Any
other wire implements this protocol; ``Robot`` does not know which it is on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from menlo.asimov._command import Command
from menlo.asimov._media import AudioChunk, Frame
from menlo.asimov._outcome import Applied, Refused
from menlo.asimov._state import State, TransportKind

StateCallback = Callable[[State], None]
FrameCallback = Callable[[Frame], None]
AudioCallback = Callable[[AudioChunk], None]
OutcomeCallback = Callable[[Applied | Refused], None]
ControllerCallback = Callable[[str | None, str | None, str], None]  # previous, current, reason


class Transport(Protocol):
    """What a wire has to provide. See the module docstring for the contract."""

    kind: TransportKind
    #: Human-readable address of the robot for this wire, e.g. ``"host:8850"``.
    endpoint: str
    #: Which of ``Capability`` this wire carries. ``drive`` and ``state`` are mandatory; a
    #: transport lists ``camera``/``microphone``/``speaker`` only when the three methods
    #: below actually deliver.
    capabilities: frozenset[str]
    #: How long ``Sent.wait_outcome`` waits by default on this wire. A LAN datagram and a
    #: different wire has a different round trip.
    default_outcome_timeout: float

    def open(self) -> None:
        """Bind, connect, start reader threads. Raise ``ConnectError`` on failure. Must be
        callable again after ``close()``: ``Robot`` reopens the same transport."""

    def close(self) -> None:
        """Stop threads, release sockets. Idempotent; never raises."""

    def send(self, command: Command) -> int:
        """Encode and send one command. Returns the sequence number stamped on it: a
        uint32 that wraps, unique per transport instance. Raise ``NotConnectedError`` when
        the transport is not open, ``LinkLostError`` when the wire is gone; never block on
        the robot."""

    def subscribe_state(self, callback: StateCallback) -> None:
        """Every telemetry sample the robot pushes, normalised to ``State``. Joint names may
        be left empty: ``Robot`` fills them from its per-robot tables. ``subscribe_*`` may be
        called before ``open()``."""

    def subscribe_outcome(self, callback: OutcomeCallback) -> None:
        """Per-command verdicts, on a wire that carries them. Asimov Edge sends none on any
        connection mode, so the shipped transports never call this."""

    def subscribe_controller_change(self, callback: ControllerCallback) -> None:
        """Who holds the body now. Same caveat as outcomes."""

    def subscribe_frames(self, callback: FrameCallback) -> None:
        """Camera frames. Raise ``UnsupportedError("camera", kind)`` when this wire has none."""

    def subscribe_audio(self, callback: AudioCallback) -> None:
        """Microphone audio. Raise ``UnsupportedError("microphone", kind)`` when this wire
        has none."""

    def play_audio(self, chunk: AudioChunk) -> None:
        """Audio to the robot's speaker. Raise ``UnsupportedError("speaker", kind)`` when this
        wire cannot carry it."""
