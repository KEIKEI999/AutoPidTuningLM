from __future__ import annotations

import copy
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Deque


class CanIfStatus(IntEnum):
    OK = 0
    ERROR = -1
    TIMEOUT = -2
    INVALID_ARG = -3
    NOT_OPEN = -4
    HW_ERROR = -5


class CanIdType(IntEnum):
    STANDARD = 0
    EXTENDED = 1


@dataclass
class CanFrame:
    id: int
    id_type: CanIdType = CanIdType.STANDARD
    dlc: int = 8
    data: bytearray = field(default_factory=lambda: bytearray(8))
    timestamp_ms: int = 0


@dataclass
class CanIfConfig:
    channel_index: int = 0
    bitrate: int = 500000
    rx_timeout_ms: int = 20


class VirtualCanBus:
    def __init__(self) -> None:
        self.handles: list["VirtualCanHandle"] = []

    def attach(self, handle: "VirtualCanHandle") -> None:
        self.handles.append(handle)

    def detach(self, handle: "VirtualCanHandle") -> None:
        self.handles = [item for item in self.handles if item is not handle]

    def broadcast(self, sender: "VirtualCanHandle", frame: CanFrame) -> None:
        for handle in self.handles:
            if handle is sender or not handle.is_open:
                continue
            handle._queue.append(copy.deepcopy(frame))


class VirtualCanHandle:
    def __init__(self, bus: VirtualCanBus, config: CanIfConfig) -> None:
        self.bus = bus
        self.config = config
        self._queue: Deque[CanFrame] = deque()
        self.is_open = False
        self.last_error: int = 0
        self.bus.attach(self)

    def open(self) -> CanIfStatus:
        self.is_open = True
        return CanIfStatus.OK

    def close(self) -> CanIfStatus:
        self.is_open = False
        return CanIfStatus.OK

    def deinit(self) -> CanIfStatus:
        self.bus.detach(self)
        self._queue.clear()
        self.is_open = False
        return CanIfStatus.OK

    def send(self, frame: CanFrame) -> CanIfStatus:
        if not self.is_open:
            self.last_error = int(CanIfStatus.NOT_OPEN)
            return CanIfStatus.NOT_OPEN
        if frame.dlc < 0 or frame.dlc > 8:
            self.last_error = int(CanIfStatus.INVALID_ARG)
            return CanIfStatus.INVALID_ARG
        self.bus.broadcast(self, frame)
        return CanIfStatus.OK

    def receive(self, timeout_ms: int | None = None) -> tuple[CanIfStatus, CanFrame | None]:
        del timeout_ms
        if not self.is_open:
            self.last_error = int(CanIfStatus.NOT_OPEN)
            return CanIfStatus.NOT_OPEN, None
        if not self._queue:
            return CanIfStatus.TIMEOUT, None
        return CanIfStatus.OK, self._queue.popleft()

    def drain(self) -> list[CanFrame]:
        frames = list(self._queue)
        self._queue.clear()
        return frames

    def get_last_error(self) -> int:
        return self.last_error


def can_if_init(bus: VirtualCanBus, config: CanIfConfig | None = None) -> VirtualCanHandle:
    return VirtualCanHandle(bus, config or CanIfConfig())


def can_if_get_time_ms() -> int:
    return int(time.monotonic() * 1000)

