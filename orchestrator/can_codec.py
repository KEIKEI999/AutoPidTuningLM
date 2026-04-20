from __future__ import annotations

import json
import math
from enum import IntEnum

from orchestrator.can_if import CanFrame, CanIdType, can_if_get_time_ms
from orchestrator.can_map import (
    CAN_DLC_CONTROL_OUTPUT,
    CAN_DLC_HEARTBEAT,
    CAN_DLC_MEASUREMENT_FB,
    CAN_DLC_SETPOINT_CMD,
    CAN_DLC_STATUS,
    CAN_ID_CONTROL_OUTPUT,
    CAN_ID_HEARTBEAT,
    CAN_ID_MEASUREMENT_FB,
    CAN_ID_SETPOINT_CMD,
    CAN_ID_STATUS,
    CAN_SCALE_DENOMINATOR,
)


class CanCodecStatus(IntEnum):
    OK = 0
    ERROR = -1
    INVALID_ARG = -2
    INVALID_ID = -3
    INVALID_DLC = -4
    RANGE_ERROR = -5


class CanCodecError(ValueError):
    def __init__(self, status: CanCodecStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def can_codec_real_to_raw(value: float) -> int:
    if not math.isfinite(value):
        raise CanCodecError(CanCodecStatus.INVALID_ARG, "value must be finite")
    scaled = round(value * CAN_SCALE_DENOMINATOR)
    if scaled > 2_147_483_647 or scaled < -2_147_483_648:
        raise CanCodecError(CanCodecStatus.RANGE_ERROR, "value is outside int32 range")
    return int(scaled)


def can_codec_raw_to_real(raw: int) -> float:
    return raw / CAN_SCALE_DENOMINATOR


def can_codec_pack_i32_le(dst: bytearray, value: int) -> None:
    for offset in range(4):
        dst[offset] = (value >> (8 * offset)) & 0xFF


def can_codec_unpack_i32_le(src: bytes | bytearray) -> int:
    raw = int(src[0]) | (int(src[1]) << 8) | (int(src[2]) << 16) | (int(src[3]) << 24)
    if raw & 0x80000000:
        raw -= 0x1_0000_0000
    return raw


def _new_frame(frame_id: int, dlc: int) -> CanFrame:
    return CanFrame(id=frame_id, id_type=CanIdType.STANDARD, dlc=dlc, data=bytearray(8), timestamp_ms=can_if_get_time_ms())


def _validate_frame(frame: CanFrame, expected_id: int, expected_dlc: int) -> None:
    if frame.id != expected_id:
        raise CanCodecError(CanCodecStatus.INVALID_ID, f"expected 0x{expected_id:03X}, got 0x{frame.id:03X}")
    if frame.dlc != expected_dlc:
        raise CanCodecError(CanCodecStatus.INVALID_DLC, f"expected DLC {expected_dlc}, got {frame.dlc}")


def _pack_scalar(value: float, frame_id: int, dlc: int) -> CanFrame:
    frame = _new_frame(frame_id, dlc)
    can_codec_pack_i32_le(frame.data, can_codec_real_to_raw(value))
    for index in range(4, 8):
        frame.data[index] = 0
    return frame


def _unpack_scalar(frame: CanFrame, frame_id: int, dlc: int) -> float:
    _validate_frame(frame, frame_id, dlc)
    return can_codec_raw_to_real(can_codec_unpack_i32_le(frame.data[:4]))


def pack_setpoint(value: float) -> CanFrame:
    return _pack_scalar(value, CAN_ID_SETPOINT_CMD, CAN_DLC_SETPOINT_CMD)


def unpack_setpoint(frame: CanFrame) -> float:
    return _unpack_scalar(frame, CAN_ID_SETPOINT_CMD, CAN_DLC_SETPOINT_CMD)


def pack_measurement(value: float) -> CanFrame:
    return _pack_scalar(value, CAN_ID_MEASUREMENT_FB, CAN_DLC_MEASUREMENT_FB)


def unpack_measurement(frame: CanFrame) -> float:
    return _unpack_scalar(frame, CAN_ID_MEASUREMENT_FB, CAN_DLC_MEASUREMENT_FB)


def pack_control_output(value: float) -> CanFrame:
    return _pack_scalar(value, CAN_ID_CONTROL_OUTPUT, CAN_DLC_CONTROL_OUTPUT)


def unpack_control_output(frame: CanFrame) -> float:
    return _unpack_scalar(frame, CAN_ID_CONTROL_OUTPUT, CAN_DLC_CONTROL_OUTPUT)


def pack_status(state_code: int, error_code: int, trial_active: int, timestamp_ms: int) -> CanFrame:
    frame = _new_frame(CAN_ID_STATUS, CAN_DLC_STATUS)
    frame.data[0] = state_code & 0xFF
    frame.data[1] = error_code & 0xFF
    frame.data[2] = trial_active & 0xFF
    frame.data[3] = 0
    for offset in range(4):
        frame.data[4 + offset] = (timestamp_ms >> (8 * offset)) & 0xFF
    return frame


def unpack_status(frame: CanFrame) -> dict[str, int]:
    _validate_frame(frame, CAN_ID_STATUS, CAN_DLC_STATUS)
    timestamp_ms = (
        frame.data[4]
        | (frame.data[5] << 8)
        | (frame.data[6] << 16)
        | (frame.data[7] << 24)
    )
    return {
        "state_code": frame.data[0],
        "error_code": frame.data[1],
        "trial_active": frame.data[2],
        "timestamp_ms": timestamp_ms,
    }


def pack_heartbeat(node_id: int, alive_counter: int) -> CanFrame:
    frame = _new_frame(CAN_ID_HEARTBEAT, CAN_DLC_HEARTBEAT)
    frame.data[0] = node_id & 0xFF
    frame.data[1] = alive_counter & 0xFF
    for index in range(2, 8):
        frame.data[index] = 0
    return frame


def unpack_heartbeat(frame: CanFrame) -> dict[str, int]:
    _validate_frame(frame, CAN_ID_HEARTBEAT, CAN_DLC_HEARTBEAT)
    return {"node_id": frame.data[0], "alive_counter": frame.data[1]}


def try_extract_json(raw_text: str) -> dict[str, object]:
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in response")
    return json.loads(raw_text[start : end + 1])
