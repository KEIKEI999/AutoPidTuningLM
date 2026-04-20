#include <limits.h>
#include <math.h>
#include <string.h>

#include "can_codec.h"

static can_codec_status_t init_scalar_frame(can_if_frame_t *frame, uint32_t id, uint8_t dlc)
{
    if (frame == 0)
    {
        return CAN_CODEC_INVALID_ARG;
    }
    memset(frame, 0, sizeof(*frame));
    frame->id = id;
    frame->id_type = CAN_IF_ID_STANDARD;
    frame->dlc = dlc;
    return CAN_CODEC_OK;
}

static can_codec_status_t validate_scalar_frame(const can_if_frame_t *frame, uint32_t expected_id, uint8_t expected_dlc)
{
    if (frame == 0)
    {
        return CAN_CODEC_INVALID_ARG;
    }
    if (frame->id != expected_id)
    {
        return CAN_CODEC_INVALID_ID;
    }
    if (frame->dlc != expected_dlc)
    {
        return CAN_CODEC_INVALID_DLC;
    }
    return CAN_CODEC_OK;
}

int32_t can_codec_real_to_raw(double value)
{
    double scaled = value * 1000.0;
    if (!isfinite(value))
    {
        return 0;
    }
    if (scaled > (double)INT32_MAX)
    {
        return INT32_MAX;
    }
    if (scaled < (double)INT32_MIN)
    {
        return INT32_MIN;
    }
    return (int32_t)llround(scaled);
}

double can_codec_raw_to_real(int32_t raw)
{
    return ((double)raw) / 1000.0;
}

void can_codec_pack_i32_le(uint8_t *dst, int32_t value)
{
    if (dst == 0)
    {
        return;
    }
    dst[0] = (uint8_t)(value & 0xFF);
    dst[1] = (uint8_t)((value >> 8) & 0xFF);
    dst[2] = (uint8_t)((value >> 16) & 0xFF);
    dst[3] = (uint8_t)((value >> 24) & 0xFF);
}

int32_t can_codec_unpack_i32_le(const uint8_t *src)
{
    if (src == 0)
    {
        return 0;
    }
    return (int32_t)(((int32_t)src[0]) | ((int32_t)src[1] << 8) | ((int32_t)src[2] << 16) | ((int32_t)src[3] << 24));
}

static can_codec_status_t pack_scalar(double value, uint32_t id, uint8_t dlc, can_if_frame_t *frame)
{
    can_codec_status_t status = init_scalar_frame(frame, id, dlc);
    if (status != CAN_CODEC_OK)
    {
        return status;
    }
    can_codec_pack_i32_le(frame->data, can_codec_real_to_raw(value));
    return CAN_CODEC_OK;
}

static can_codec_status_t unpack_scalar(const can_if_frame_t *frame, uint32_t id, uint8_t dlc, double *value)
{
    can_codec_status_t status;
    if (value == 0)
    {
        return CAN_CODEC_INVALID_ARG;
    }
    status = validate_scalar_frame(frame, id, dlc);
    if (status != CAN_CODEC_OK)
    {
        return status;
    }
    *value = can_codec_raw_to_real(can_codec_unpack_i32_le(frame->data));
    return CAN_CODEC_OK;
}

can_codec_status_t can_codec_pack_setpoint(double setpoint, can_if_frame_t *frame)
{
    return pack_scalar(setpoint, CAN_ID_SETPOINT_CMD, CAN_DLC_SETPOINT_CMD, frame);
}

can_codec_status_t can_codec_unpack_setpoint(const can_if_frame_t *frame, double *setpoint)
{
    return unpack_scalar(frame, CAN_ID_SETPOINT_CMD, CAN_DLC_SETPOINT_CMD, setpoint);
}

can_codec_status_t can_codec_pack_measurement(double measurement, can_if_frame_t *frame)
{
    return pack_scalar(measurement, CAN_ID_MEASUREMENT_FB, CAN_DLC_MEASUREMENT_FB, frame);
}

can_codec_status_t can_codec_unpack_measurement(const can_if_frame_t *frame, double *measurement)
{
    return unpack_scalar(frame, CAN_ID_MEASUREMENT_FB, CAN_DLC_MEASUREMENT_FB, measurement);
}

can_codec_status_t can_codec_pack_control_output(double control_output, can_if_frame_t *frame)
{
    return pack_scalar(control_output, CAN_ID_CONTROL_OUTPUT, CAN_DLC_CONTROL_OUTPUT, frame);
}

can_codec_status_t can_codec_unpack_control_output(const can_if_frame_t *frame, double *control_output)
{
    return unpack_scalar(frame, CAN_ID_CONTROL_OUTPUT, CAN_DLC_CONTROL_OUTPUT, control_output);
}

can_codec_status_t can_codec_pack_status(uint8_t state_code, uint8_t error_code, uint8_t trial_active, uint32_t timestamp_ms, can_if_frame_t *frame)
{
    can_codec_status_t status = init_scalar_frame(frame, CAN_ID_STATUS, CAN_DLC_STATUS);
    if (status != CAN_CODEC_OK)
    {
        return status;
    }
    frame->data[0] = state_code;
    frame->data[1] = error_code;
    frame->data[2] = trial_active;
    frame->data[3] = 0U;
    frame->data[4] = (uint8_t)(timestamp_ms & 0xFFU);
    frame->data[5] = (uint8_t)((timestamp_ms >> 8) & 0xFFU);
    frame->data[6] = (uint8_t)((timestamp_ms >> 16) & 0xFFU);
    frame->data[7] = (uint8_t)((timestamp_ms >> 24) & 0xFFU);
    return CAN_CODEC_OK;
}

can_codec_status_t can_codec_unpack_status(const can_if_frame_t *frame, uint8_t *state_code, uint8_t *error_code, uint8_t *trial_active, uint32_t *timestamp_ms)
{
    can_codec_status_t status = validate_scalar_frame(frame, CAN_ID_STATUS, CAN_DLC_STATUS);
    if (status != CAN_CODEC_OK)
    {
        return status;
    }
    if ((state_code == 0) || (error_code == 0) || (trial_active == 0) || (timestamp_ms == 0))
    {
        return CAN_CODEC_INVALID_ARG;
    }
    *state_code = frame->data[0];
    *error_code = frame->data[1];
    *trial_active = frame->data[2];
    *timestamp_ms = ((uint32_t)frame->data[4])
        | ((uint32_t)frame->data[5] << 8)
        | ((uint32_t)frame->data[6] << 16)
        | ((uint32_t)frame->data[7] << 24);
    return CAN_CODEC_OK;
}

can_codec_status_t can_codec_pack_heartbeat(uint8_t node_id, uint8_t alive_counter, can_if_frame_t *frame)
{
    can_codec_status_t status = init_scalar_frame(frame, CAN_ID_HEARTBEAT, CAN_DLC_HEARTBEAT);
    if (status != CAN_CODEC_OK)
    {
        return status;
    }
    frame->data[0] = node_id;
    frame->data[1] = alive_counter;
    return CAN_CODEC_OK;
}

can_codec_status_t can_codec_unpack_heartbeat(const can_if_frame_t *frame, uint8_t *node_id, uint8_t *alive_counter)
{
    can_codec_status_t status = validate_scalar_frame(frame, CAN_ID_HEARTBEAT, CAN_DLC_HEARTBEAT);
    if (status != CAN_CODEC_OK)
    {
        return status;
    }
    if ((node_id == 0) || (alive_counter == 0))
    {
        return CAN_CODEC_INVALID_ARG;
    }
    *node_id = frame->data[0];
    *alive_counter = frame->data[1];
    return CAN_CODEC_OK;
}
