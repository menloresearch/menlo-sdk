"""Camera frames, microphone audio and speaker output, typed.

The robot's edge owns the sensors; the SDK owns the shape a script sees. A transport
that carries a stream delivers it through ``subscribe_frames`` / ``subscribe_audio`` and
accepts ``play_audio``; one that does not raises :class:`UnsupportedError` from
:meth:`Camera.latest` and friends with the reason in the message. Check
``robot.has("camera")`` first when a script should degrade instead of fail.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from asimov_sdk._errors import UnsupportedError, WaitTimeoutError

if TYPE_CHECKING:
    from asimov_sdk.transport.base import Transport

ImageEncoding = Literal["rgb8", "bgr8", "gray8", "yuv420", "jpeg", "h264", "unknown"]
AudioEncoding = Literal["pcm_s16le", "pcm_f32le", "opus", "unknown"]


@dataclass(frozen=True, slots=True)
class Frame:
    """One complete camera frame. ``data`` is the raw or encoded bytes as the edge sent them;
    ``stride_bytes`` is 0 for encoded frames. Decode with your imaging library of choice."""

    width: int
    height: int
    encoding: ImageEncoding
    data: bytes
    stride_bytes: int = 0
    key_frame: bool = True
    frame_id: str = "camera"
    timestamp_ns: int = 0  # edge clock at capture; 0 when the transport did not carry one
    sequence: int = 0

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """One block of audio, microphone in or speaker out."""

    sample_rate_hz: int
    channels: int
    samples_per_channel: int
    encoding: AudioEncoding
    data: bytes
    stream_id: str = "microphone"
    timestamp_ns: int = 0
    sequence: int = 0

    @property
    def duration_s(self) -> float:
        return self.samples_per_channel / self.sample_rate_hz if self.sample_rate_hz else 0.0


class _Stream[T]:
    """Latest-value store with a condition variable; the base of Camera and Microphone."""

    def __init__(self, capability: str, transport: Transport) -> None:
        self._capability = capability
        self._tx = transport
        self._latest: T | None = None
        self._count = 0
        self._cv = threading.Condition()
        self._subscribers: list[Callable[[T], None]] = []
        self._attached = False

    def _require(self) -> None:
        if self._capability not in self._tx.capabilities:
            raise UnsupportedError(self._capability, self._tx.kind)

    def _attach(self) -> None:
        raise NotImplementedError

    def _ensure(self) -> None:
        self._require()
        if not self._attached:
            self._attach()
            self._attached = True

    def _on_item(self, item: T) -> None:
        with self._cv:
            self._latest = item
            self._count += 1
            self._cv.notify_all()
        for cb in tuple(self._subscribers):
            cb(item)

    def latest(self) -> T | None:
        """The most recent item, or ``None`` when nothing has arrived. Never blocks."""
        self._ensure()
        return self._latest

    def subscribe(self, callback: Callable[[T], None]) -> None:
        """Call ``callback`` on the transport's reader thread for every item. Keep it short."""
        self._ensure()
        self._subscribers.append(callback)

    def _wait_past(self, seen: int, timeout: float) -> T | None:
        """Block until more than ``seen`` items have arrived; the newest, or None on timeout."""
        with self._cv:
            if not self._cv.wait_for(lambda: self._count > seen, timeout):
                return None
            return self._latest

    def stream(self, *, timeout: float = 5.0) -> Iterator[T]:
        """Yield items as they arrive. Raises :class:`WaitTimeoutError` when ``timeout`` seconds
        pass without one — a stream that has gone quiet is a fact, not an idle loop."""
        self._ensure()
        seen = self._count
        while True:
            item = self._wait_past(seen, timeout)
            if item is None:
                raise WaitTimeoutError(f"no {self._capability} data for {timeout:.1f}s", last=None)
            seen = self._count
            yield item


class Camera(_Stream[Frame]):
    """``robot.camera``: the robot's camera as :class:`Frame` objects."""

    def _attach(self) -> None:
        self._tx.subscribe_frames(self._on_item)

    def frames(self, *, timeout: float = 5.0) -> Iterator[Frame]:
        """Frames as they arrive; see :meth:`_Stream.stream`."""
        return self.stream(timeout=timeout)


class Microphone(_Stream[AudioChunk]):
    """``robot.microphone``: the robot's microphone as :class:`AudioChunk` objects."""

    def _attach(self) -> None:
        self._tx.subscribe_audio(self._on_item)

    def chunks(self, *, timeout: float = 5.0) -> Iterator[AudioChunk]:
        """Audio as it arrives; see :meth:`_Stream.stream`."""
        return self.stream(timeout=timeout)


class Speaker:
    """``robot.speaker``: play audio on the robot."""

    def __init__(self, transport: Transport) -> None:
        self._tx = transport

    def play(self, chunk: AudioChunk) -> None:
        """Send one chunk to the robot's speaker. Raises :class:`UnsupportedError` when this
        transport does not carry audio to the robot."""
        if "speaker" not in self._tx.capabilities:
            raise UnsupportedError("speaker", self._tx.kind)
        self._tx.play_audio(chunk)

    def play_pcm(
        self, pcm_s16le: bytes, *, sample_rate_hz: int = 16_000, channels: int = 1
    ) -> None:
        """Convenience for raw 16-bit little-endian PCM."""
        frame_bytes = 2 * channels
        self.play(
            AudioChunk(
                sample_rate_hz=sample_rate_hz,
                channels=channels,
                samples_per_channel=len(pcm_s16le) // frame_bytes,
                encoding="pcm_s16le",
                data=pcm_s16le,
                stream_id="speaker",
                timestamp_ns=time.time_ns(),
            )
        )
