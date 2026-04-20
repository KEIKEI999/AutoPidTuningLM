from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

from orchestrator.can_if import CanFrame, CanIdType, CanIfConfig, CanIfStatus

XL_SUCCESS = 0
XL_ERR_QUEUE_IS_EMPTY = 10
XL_INVALID_PORTHANDLE = -1
XL_INTERFACE_VERSION = 3
XL_BUS_TYPE_CAN = 1
XL_ACTIVATE_RESET_CLOCK = 8
XL_TRANSMIT_MSG = 10
XL_RECEIVE_MSG = 1
XL_CAN_EXT_MSG_ID = 0x80000000
XL_MAX_LENGTH = 31
XL_CONFIG_MAX_CHANNELS = 64


class VectorCanError(RuntimeError):
    pass


class _XLbusData(ctypes.Union):
    _fields_ = [("raw", ctypes.c_ubyte * 28)]


class _XLbusParams(ctypes.Structure):
    _fields_ = [("busType", ctypes.c_uint), ("data", _XLbusData)]


class _XLchannelConfig(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * (XL_MAX_LENGTH + 1)),
        ("hwType", ctypes.c_ubyte),
        ("hwIndex", ctypes.c_ubyte),
        ("hwChannel", ctypes.c_ubyte),
        ("transceiverType", ctypes.c_ushort),
        ("transceiverState", ctypes.c_ushort),
        ("configError", ctypes.c_ushort),
        ("channelIndex", ctypes.c_ubyte),
        ("channelMask", ctypes.c_ulonglong),
        ("channelCapabilities", ctypes.c_uint),
        ("channelBusCapabilities", ctypes.c_uint),
        ("isOnBus", ctypes.c_ubyte),
        ("connectedBusType", ctypes.c_uint),
        ("busParams", _XLbusParams),
        ("_doNotUse", ctypes.c_uint),
        ("driverVersion", ctypes.c_uint),
        ("interfaceVersion", ctypes.c_uint),
        ("raw_data", ctypes.c_uint * 10),
        ("serialNumber", ctypes.c_uint),
        ("articleNumber", ctypes.c_uint),
        ("transceiverName", ctypes.c_char * (XL_MAX_LENGTH + 1)),
        ("specialCabFlags", ctypes.c_uint),
        ("dominantTimeout", ctypes.c_uint),
        ("dominantRecessiveDelay", ctypes.c_ubyte),
        ("recessiveDominantDelay", ctypes.c_ubyte),
        ("connectionInfo", ctypes.c_ubyte),
        ("currentlyAvailableTimestamps", ctypes.c_ubyte),
        ("minimalSupplyVoltage", ctypes.c_ushort),
        ("maximalSupplyVoltage", ctypes.c_ushort),
        ("maximalBaudrate", ctypes.c_uint),
        ("fpgaCoreCapabilities", ctypes.c_ubyte),
        ("specialDeviceStatus", ctypes.c_ubyte),
        ("channelBusActiveCapabilities", ctypes.c_ushort),
        ("breakOffset", ctypes.c_ushort),
        ("delimiterOffset", ctypes.c_ushort),
        ("reserved", ctypes.c_uint * 3),
    ]


class _XLdriverConfig(ctypes.Structure):
    _fields_ = [
        ("dllVersion", ctypes.c_uint),
        ("channelCount", ctypes.c_uint),
        ("reserved", ctypes.c_uint * 10),
        ("channel", _XLchannelConfig * XL_CONFIG_MAX_CHANNELS),
    ]


class _XLCanMsg(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint),
        ("flags", ctypes.c_ushort),
        ("dlc", ctypes.c_ushort),
        ("res1", ctypes.c_ulonglong),
        ("data", ctypes.c_ubyte * 8),
        ("res2", ctypes.c_ulonglong),
    ]


class _XLTagData(ctypes.Union):
    _fields_ = [("msg", _XLCanMsg), ("raw", ctypes.c_ubyte * 32)]


class _XLevent(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_ubyte),
        ("chanIndex", ctypes.c_ubyte),
        ("transId", ctypes.c_ushort),
        ("portHandle", ctypes.c_ushort),
        ("flags", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("timeStamp", ctypes.c_ulonglong),
        ("tagData", _XLTagData),
    ]


if ctypes.sizeof(_XLCanMsg) != 32 or ctypes.sizeof(_XLevent) != 48:
    raise RuntimeError("Vector XL ctypes layout does not match expected structure sizes.")


def _detect_sdk_dir() -> Path:
    env_value = os.environ.get("VECTOR_XL_SDK_DIR")
    if env_value:
        path = Path(env_value).expanduser()
        if path.exists():
            return path
    public_root = Path(r"C:\Users\Public\Documents\Vector")
    candidates = sorted(public_root.glob("XL Driver Library *"), reverse=True)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise VectorCanError("VECTOR_XL_SDK_DIR is not set and no local Vector XL SDK installation was found.")


class VectorXlLibrary:
    def __init__(self, sdk_dir: Path | None = None) -> None:
        self.sdk_dir = (sdk_dir or _detect_sdk_dir()).resolve()
        dll_name = "vxlapi64.dll" if ctypes.sizeof(ctypes.c_void_p) == 8 else "vxlapi.dll"
        self.dll_path = self.sdk_dir / "bin" / dll_name
        if not self.dll_path.exists():
            raise VectorCanError(f"Vector XL DLL not found: {self.dll_path}")
        os.environ["PATH"] = str(self.dll_path.parent) + os.pathsep + os.environ.get("PATH", "")
        self.dll = ctypes.WinDLL(str(self.dll_path))
        self._open_count = 0
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self.dll.xlOpenDriver.restype = ctypes.c_short
        self.dll.xlCloseDriver.restype = ctypes.c_short
        self.dll.xlGetApplConfig.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.c_uint,
        ]
        self.dll.xlGetApplConfig.restype = ctypes.c_short
        self.dll.xlGetDriverConfig.argtypes = [ctypes.POINTER(_XLdriverConfig)]
        self.dll.xlGetDriverConfig.restype = ctypes.c_short
        self.dll.xlGetChannelMask.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.xlGetChannelMask.restype = ctypes.c_ulonglong
        self.dll.xlOpenPort.argtypes = [
            ctypes.POINTER(ctypes.c_longlong),
            ctypes.c_char_p,
            ctypes.c_ulonglong,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self.dll.xlOpenPort.restype = ctypes.c_short
        self.dll.xlCanSetChannelBitrate.argtypes = [ctypes.c_longlong, ctypes.c_ulonglong, ctypes.c_uint]
        self.dll.xlCanSetChannelBitrate.restype = ctypes.c_short
        self.dll.xlActivateChannel.argtypes = [ctypes.c_longlong, ctypes.c_ulonglong, ctypes.c_uint, ctypes.c_uint]
        self.dll.xlActivateChannel.restype = ctypes.c_short
        self.dll.xlCanTransmit.argtypes = [
            ctypes.c_longlong,
            ctypes.c_ulonglong,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(_XLevent),
        ]
        self.dll.xlCanTransmit.restype = ctypes.c_short
        self.dll.xlReceive.argtypes = [
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(_XLevent),
        ]
        self.dll.xlReceive.restype = ctypes.c_short
        self.dll.xlDeactivateChannel.argtypes = [ctypes.c_longlong, ctypes.c_ulonglong]
        self.dll.xlDeactivateChannel.restype = ctypes.c_short
        self.dll.xlClosePort.argtypes = [ctypes.c_longlong]
        self.dll.xlClosePort.restype = ctypes.c_short
        self.dll.xlGetErrorString.argtypes = [ctypes.c_short]
        self.dll.xlGetErrorString.restype = ctypes.c_char_p

    def error_text(self, status: int) -> str:
        raw = self.dll.xlGetErrorString(status)
        if not raw:
            return f"XLstatus={status}"
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return f"XLstatus={status}"

    def open_driver(self) -> None:
        if self._open_count == 0:
            status = int(self.dll.xlOpenDriver())
            if status != XL_SUCCESS:
                raise VectorCanError(f"xlOpenDriver failed: {self.error_text(status)}")
        self._open_count += 1

    def close_driver(self) -> None:
        if self._open_count <= 0:
            return
        self._open_count -= 1
        if self._open_count == 0:
            status = int(self.dll.xlCloseDriver())
            if status != XL_SUCCESS:
                raise VectorCanError(f"xlCloseDriver failed: {self.error_text(status)}")

    def resolve_channel(self, channel_index: int, *, app_name: bytes = b"AutoTuningLM") -> tuple[int, int, int, int]:
        hw_type = ctypes.c_uint()
        hw_index = ctypes.c_uint()
        hw_channel = ctypes.c_uint()
        status = int(
            self.dll.xlGetApplConfig(
                app_name,
                channel_index,
                ctypes.byref(hw_type),
                ctypes.byref(hw_index),
                ctypes.byref(hw_channel),
                XL_BUS_TYPE_CAN,
            )
        )
        if status == XL_SUCCESS and hw_channel.value != 0xFFFFFFFF:
            mask = int(self.dll.xlGetChannelMask(hw_type.value, hw_index.value, hw_channel.value))
            if mask != 0:
                return int(hw_type.value), int(hw_index.value), int(hw_channel.value), mask

        driver_config = _XLdriverConfig()
        status = int(self.dll.xlGetDriverConfig(ctypes.byref(driver_config)))
        if status != XL_SUCCESS:
            raise VectorCanError(f"xlGetDriverConfig failed: {self.error_text(status)}")

        discovered: list[tuple[int, int, int, int]] = []
        for slot in range(min(int(driver_config.channelCount), XL_CONFIG_MAX_CHANNELS)):
            channel = driver_config.channel[slot]
            name = bytes(channel.name).split(b"\0", 1)[0].decode(errors="ignore").strip()
            if not name:
                continue
            mask = int(self.dll.xlGetChannelMask(int(channel.hwType), int(channel.hwIndex), int(channel.hwChannel)))
            if mask == 0:
                continue
            discovered.append((int(channel.hwType), int(channel.hwIndex), int(channel.hwChannel), mask))

        if channel_index >= len(discovered):
            raise VectorCanError(
                f"Requested channel_index={channel_index}, but only {len(discovered)} Vector channels were discovered."
            )
        return discovered[channel_index]


class VectorCanHandle:
    def __init__(self, library: VectorXlLibrary, config: CanIfConfig, *, app_name: str) -> None:
        self.library = library
        self.config = config
        self.app_name = app_name.encode("ascii", errors="ignore") or b"AutoTuningLMPy"
        self.port_handle = ctypes.c_longlong(XL_INVALID_PORTHANDLE)
        self.channel_mask = ctypes.c_ulonglong(0)
        self.permission_mask = ctypes.c_ulonglong(0)
        self.is_open = False
        self.last_error = 0
        self._driver_ref = False

    def open(self) -> CanIfStatus:
        try:
            self.library.open_driver()
            self._driver_ref = True
            hw_type, hw_index, hw_channel, mask = self.library.resolve_channel(self.config.channel_index)
            del hw_type, hw_index, hw_channel
            self.channel_mask = ctypes.c_ulonglong(mask)
            self.permission_mask = ctypes.c_ulonglong(mask)
            status = int(
                self.library.dll.xlOpenPort(
                    ctypes.byref(self.port_handle),
                    self.app_name,
                    self.channel_mask,
                    ctypes.byref(self.permission_mask),
                    1024,
                    XL_INTERFACE_VERSION,
                    XL_BUS_TYPE_CAN,
                )
            )
            if status != XL_SUCCESS:
                self.last_error = status
                self._release_driver()
                return CanIfStatus.HW_ERROR
            if int(self.permission_mask.value) != 0:
                status = int(
                    self.library.dll.xlCanSetChannelBitrate(
                        self.port_handle,
                        self.permission_mask,
                        int(self.config.bitrate),
                    )
                )
                if status != XL_SUCCESS:
                    self.last_error = status
                    self.close()
                    return CanIfStatus.HW_ERROR
            status = int(
                self.library.dll.xlActivateChannel(
                    self.port_handle,
                    self.channel_mask,
                    XL_BUS_TYPE_CAN,
                    XL_ACTIVATE_RESET_CLOCK,
                )
            )
            self.last_error = status
            if status != XL_SUCCESS:
                self.close()
                return CanIfStatus.HW_ERROR
            self.is_open = True
            return CanIfStatus.OK
        except VectorCanError:
            self.last_error = -1
            self._release_driver()
            return CanIfStatus.HW_ERROR

    def close(self) -> CanIfStatus:
        if self.port_handle.value != XL_INVALID_PORTHANDLE:
            if self.channel_mask.value != 0:
                self.library.dll.xlDeactivateChannel(self.port_handle, self.channel_mask)
            self.library.dll.xlClosePort(self.port_handle)
            self.port_handle = ctypes.c_longlong(XL_INVALID_PORTHANDLE)
        self.is_open = False
        self._release_driver()
        return CanIfStatus.OK

    def deinit(self) -> CanIfStatus:
        return self.close()

    def send(self, frame: CanFrame) -> CanIfStatus:
        if not self.is_open:
            self.last_error = int(CanIfStatus.NOT_OPEN)
            return CanIfStatus.NOT_OPEN
        if frame.dlc < 0 or frame.dlc > 8:
            self.last_error = int(CanIfStatus.INVALID_ARG)
            return CanIfStatus.INVALID_ARG
        event = _XLevent()
        event.tag = XL_TRANSMIT_MSG
        event.tagData.msg.id = int(frame.id)
        event.tagData.msg.flags = XL_CAN_EXT_MSG_ID if frame.id_type == CanIdType.EXTENDED else 0
        event.tagData.msg.dlc = int(frame.dlc)
        for index in range(frame.dlc):
            event.tagData.msg.data[index] = int(frame.data[index])
        count = ctypes.c_uint(1)
        status = int(self.library.dll.xlCanTransmit(self.port_handle, self.channel_mask, ctypes.byref(count), ctypes.byref(event)))
        self.last_error = status
        return CanIfStatus.OK if status == XL_SUCCESS else CanIfStatus.HW_ERROR

    def receive(self, timeout_ms: int | None = None) -> tuple[CanIfStatus, CanFrame | None]:
        if not self.is_open:
            self.last_error = int(CanIfStatus.NOT_OPEN)
            return CanIfStatus.NOT_OPEN, None
        effective_timeout = self.config.rx_timeout_ms if timeout_ms is None else max(0, int(timeout_ms))
        deadline = time.monotonic() + (effective_timeout / 1000.0)
        while True:
            event = _XLevent()
            count = ctypes.c_uint(1)
            status = int(self.library.dll.xlReceive(self.port_handle, ctypes.byref(count), ctypes.byref(event)))
            self.last_error = status
            if status == XL_SUCCESS and event.tag == XL_RECEIVE_MSG:
                frame = CanFrame(
                    id=int(event.tagData.msg.id & 0x1FFFFFFF),
                    id_type=CanIdType.EXTENDED if (event.tagData.msg.flags & XL_CAN_EXT_MSG_ID) else CanIdType.STANDARD,
                    dlc=int(event.tagData.msg.dlc),
                    data=bytearray(int(event.tagData.msg.data[index]) for index in range(8)),
                    timestamp_ms=int(event.timeStamp // 1_000_000),
                )
                return CanIfStatus.OK, frame
            if status != XL_ERR_QUEUE_IS_EMPTY:
                return CanIfStatus.HW_ERROR, None
            if time.monotonic() >= deadline:
                return CanIfStatus.TIMEOUT, None
            time.sleep(0.001)

    def drain(self) -> list[CanFrame]:
        frames: list[CanFrame] = []
        while True:
            status, frame = self.receive(0)
            if status != CanIfStatus.OK or frame is None:
                return frames
            frames.append(frame)

    def get_last_error(self) -> int:
        return self.last_error

    def _release_driver(self) -> None:
        if self._driver_ref:
            self._driver_ref = False
            self.library.close_driver()


def can_if_init_vector_xl(
    config: CanIfConfig | None = None,
    *,
    app_name: str = "AutoTuningLMPy",
    library: VectorXlLibrary | None = None,
) -> VectorCanHandle:
    return VectorCanHandle(library or VectorXlLibrary(), config or CanIfConfig(), app_name=app_name)
