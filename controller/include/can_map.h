#ifndef CAN_MAP_H
#define CAN_MAP_H

#include <stdint.h>

#define CAN_ID_SETPOINT_CMD      (0x100U)
#define CAN_ID_MEASUREMENT_FB    (0x101U)
#define CAN_ID_CONTROL_OUTPUT    (0x102U)
#define CAN_ID_STATUS            (0x103U)
#define CAN_ID_HEARTBEAT         (0x104U)

#define CAN_DLC_SETPOINT_CMD     (8U)
#define CAN_DLC_MEASUREMENT_FB   (8U)
#define CAN_DLC_CONTROL_OUTPUT   (8U)
#define CAN_DLC_STATUS           (8U)
#define CAN_DLC_HEARTBEAT        (8U)

#define CAN_SCALE_NUMERATOR      (1)
#define CAN_SCALE_DENOMINATOR    (1000)

#define CAN_STATE_INIT           (0U)
#define CAN_STATE_READY          (1U)
#define CAN_STATE_RUNNING        (2U)
#define CAN_STATE_ERROR          (3U)
#define CAN_STATE_FINISHED       (4U)

#define CAN_NODE_ID_CONTROLLER   (1U)
#define CAN_NODE_ID_PLANT        (2U)
#define CAN_NODE_ID_ORCH         (3U)

#endif /* CAN_MAP_H */

