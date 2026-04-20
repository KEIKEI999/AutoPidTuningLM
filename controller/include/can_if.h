#ifndef CAN_IF_H
#define CAN_IF_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum
{
    CAN_IF_OK = 0,
    CAN_IF_ERROR = -1,
    CAN_IF_TIMEOUT = -2,
    CAN_IF_INVALID_ARG = -3,
    CAN_IF_NOT_OPEN = -4,
    CAN_IF_HW_ERROR = -5
} can_if_status_t;

typedef enum
{
    CAN_IF_ID_STANDARD = 0,
    CAN_IF_ID_EXTENDED = 1
} can_if_id_type_t;

typedef struct
{
    uint32_t id;
    can_if_id_type_t id_type;
    uint8_t dlc;
    uint8_t data[8];
    uint32_t timestamp_ms;
} can_if_frame_t;

typedef struct
{
    uint32_t channel_index;
    uint32_t bitrate;
    uint32_t rx_timeout_ms;
} can_if_config_t;

typedef struct can_if_handle_tag can_if_handle_t;

can_if_status_t can_if_init(can_if_handle_t **handle, const can_if_config_t *config);
can_if_status_t can_if_open(can_if_handle_t *handle);
can_if_status_t can_if_close(can_if_handle_t *handle);
can_if_status_t can_if_deinit(can_if_handle_t *handle);
can_if_status_t can_if_send(can_if_handle_t *handle, const can_if_frame_t *frame);
can_if_status_t can_if_receive(can_if_handle_t *handle, can_if_frame_t *frame, uint32_t timeout_ms);
can_if_status_t can_if_get_last_error(can_if_handle_t *handle, int32_t *error_code);
uint32_t can_if_get_time_ms(void);

#ifdef __cplusplus
}
#endif

#endif /* CAN_IF_H */

