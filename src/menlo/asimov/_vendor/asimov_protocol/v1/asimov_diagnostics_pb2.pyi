from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Alert(_message.Message):
    __slots__ = ("id", "severity", "value", "threshold", "first_set_us", "source_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    FIRST_SET_US_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    severity: int
    value: int
    threshold: int
    first_set_us: int
    source_id: int
    def __init__(self, id: _Optional[int] = ..., severity: _Optional[int] = ..., value: _Optional[int] = ..., threshold: _Optional[int] = ..., first_set_us: _Optional[int] = ..., source_id: _Optional[int] = ...) -> None: ...

class ThreadMetrics(_message.Message):
    __slots__ = ("max_cycle_us", "avg_cycle_us", "jitter_us")
    MAX_CYCLE_US_FIELD_NUMBER: _ClassVar[int]
    AVG_CYCLE_US_FIELD_NUMBER: _ClassVar[int]
    JITTER_US_FIELD_NUMBER: _ClassVar[int]
    max_cycle_us: int
    avg_cycle_us: int
    jitter_us: int
    def __init__(self, max_cycle_us: _Optional[int] = ..., avg_cycle_us: _Optional[int] = ..., jitter_us: _Optional[int] = ...) -> None: ...

class OnnxMetrics(_message.Message):
    __slots__ = ("avg_inference_ms", "max_inference_ms", "inference_count")
    AVG_INFERENCE_MS_FIELD_NUMBER: _ClassVar[int]
    MAX_INFERENCE_MS_FIELD_NUMBER: _ClassVar[int]
    INFERENCE_COUNT_FIELD_NUMBER: _ClassVar[int]
    avg_inference_ms: float
    max_inference_ms: float
    inference_count: int
    def __init__(self, avg_inference_ms: _Optional[float] = ..., max_inference_ms: _Optional[float] = ..., inference_count: _Optional[int] = ...) -> None: ...

class CanMetrics(_message.Message):
    __slots__ = ("frames_sent", "frames_received", "errors", "bus_offs")
    FRAMES_SENT_FIELD_NUMBER: _ClassVar[int]
    FRAMES_RECEIVED_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    BUS_OFFS_FIELD_NUMBER: _ClassVar[int]
    frames_sent: int
    frames_received: int
    errors: int
    bus_offs: int
    def __init__(self, frames_sent: _Optional[int] = ..., frames_received: _Optional[int] = ..., errors: _Optional[int] = ..., bus_offs: _Optional[int] = ...) -> None: ...
