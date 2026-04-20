#ifndef CAN_CODEC_H
#define CAN_CODEC_H

#include <stdint.h>
#include "can_if.h"
#include "can_map.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum
{
    CAN_CODEC_OK = 0,
    CAN_CODEC_ERROR = -1,
    CAN_CODEC_INVALID_ARG = -2,
    CAN_CODEC_INVALID_ID = -3,
    CAN_CODEC_INVALID_DLC = -4,
    CAN_CODEC_RANGE_ERROR = -5
} can_codec_status_t;

can_codec_status_t can_codec_pack_setpoint(double setpoint, can_if_frame_t *frame);
can_codec_status_t can_codec_unpack_setpoint(const can_if_frame_t *frame, double *setpoint);
can_codec_status_t can_codec_pack_measurement(double measurement, can_if_frame_t *frame);
can_codec_status_t can_codec_unpack_measurement(const can_if_frame_t *frame, double *measurement);
can_codec_status_t can_codec_pack_control_output(double control_output, can_if_frame_t *frame);
can_codec_status_t can_codec_unpack_control_output(const can_if_frame_t *frame, double *control_output);
can_codec_status_t can_codec_pack_status(uint8_t state_code, uint8_t error_code, uint8_t trial_active, uint32_t timestamp_ms, can_if_frame_t *frame);
can_codec_status_t can_codec_unpack_status(const can_if_frame_t *frame, uint8_t *state_code, uint8_t *error_code, uint8_t *trial_active, uint32_t *timestamp_ms);
can_codec_status_t can_codec_pack_heartbeat(uint8_t node_id, uint8_t alive_counter, can_if_frame_t *frame);
can_codec_status_t can_codec_unpack_heartbeat(const can_if_frame_t *frame, uint8_t *node_id, uint8_t *alive_counter);
int32_t can_codec_real_to_raw(double value);
double can_codec_raw_to_real(int32_t raw);
void can_codec_pack_i32_le(uint8_t *dst, int32_t value);
int32_t can_codec_unpack_i32_le(const uint8_t *src);

#ifdef __cplusplus
}
#endif

#endif /* CAN_CODEC_H */

