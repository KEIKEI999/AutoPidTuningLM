#include "control_loop.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "can_codec.h"
#include "can_if.h"
#include "can_map.h"
#include "pid_controller.h"

#define CONTROL_LOOP_DEFAULT_CHANNEL_INDEX     (0U)
#define CONTROL_LOOP_DEFAULT_BITRATE           (500000U)
#define CONTROL_LOOP_DEFAULT_RX_TIMEOUT_MS     (20U)
#define CONTROL_LOOP_DEFAULT_STEPS             (20U)
#define CONTROL_LOOP_DEFAULT_DT_SEC            (0.01)
#define CONTROL_LOOP_DEFAULT_CONTROL_LIMIT     (2.0)

typedef struct
{
    uint32_t channel_index;
    uint32_t bitrate;
    uint32_t rx_timeout_ms;
    uint32_t steps;
    double dt_sec;
    double control_limit;
} control_loop_config_t;

typedef struct
{
    double setpoint;
    double measurement;
    uint8_t orchestrator_alive_counter;
    uint8_t plant_alive_counter;
    int has_setpoint;
    int has_measurement;
    int has_orchestrator_heartbeat;
    int has_plant_heartbeat;
} controller_inputs_t;

static void log_stdout_line(const char *label, uint32_t step, const char *message)
{
    printf("[controller][step %lu][%s] %s\n", (unsigned long)step, label, message);
    fflush(stdout);
}

static void log_stderr_line(const char *label, uint32_t step, const char *message)
{
    fprintf(stderr, "[controller][step %lu][%s] %s\n", (unsigned long)step, label, message);
    fflush(stderr);
}

static int report_can_error(const char *label, can_if_status_t status, can_if_handle_t *handle)
{
    int32_t last_error = 0;

    if (handle != 0)
    {
        (void)can_if_get_last_error(handle, &last_error);
    }
    fprintf(stderr, "%s failed: can_status=%d xl_status=%ld\n", label, (int)status, (long)last_error);
    return 1;
}

static uint32_t read_env_u32(const char *name, uint32_t fallback_value)
{
    char *raw = 0;
    size_t raw_length = 0U;
    char *end_ptr = 0;
    unsigned long value;

    if ((_dupenv_s(&raw, &raw_length, name) != 0) || (raw == 0) || (*raw == '\0'))
    {
        return fallback_value;
    }
    value = strtoul(raw, &end_ptr, 10);
    if ((end_ptr == raw) || (*end_ptr != '\0'))
    {
        free(raw);
        return fallback_value;
    }
    free(raw);
    return (uint32_t)value;
}

static double read_env_double(const char *name, double fallback_value)
{
    char *raw = 0;
    size_t raw_length = 0U;
    char *end_ptr = 0;
    double value;

    if ((_dupenv_s(&raw, &raw_length, name) != 0) || (raw == 0) || (*raw == '\0'))
    {
        return fallback_value;
    }
    value = strtod(raw, &end_ptr);
    if ((end_ptr == raw) || (*end_ptr != '\0'))
    {
        free(raw);
        return fallback_value;
    }
    free(raw);
    return value;
}

static void load_control_loop_config(control_loop_config_t *config)
{
    memset(config, 0, sizeof(*config));
    config->channel_index = read_env_u32("ATLM_CHANNEL_INDEX", CONTROL_LOOP_DEFAULT_CHANNEL_INDEX);
    config->bitrate = read_env_u32("ATLM_BITRATE", CONTROL_LOOP_DEFAULT_BITRATE);
    config->rx_timeout_ms = read_env_u32("ATLM_RX_TIMEOUT_MS", CONTROL_LOOP_DEFAULT_RX_TIMEOUT_MS);
    config->steps = read_env_u32("ATLM_STEPS", CONTROL_LOOP_DEFAULT_STEPS);
    config->dt_sec = read_env_double("ATLM_DT_SEC", CONTROL_LOOP_DEFAULT_DT_SEC);
    config->control_limit = read_env_double("ATLM_CONTROL_LIMIT", CONTROL_LOOP_DEFAULT_CONTROL_LIMIT);
    if (config->steps == 0U)
    {
        config->steps = CONTROL_LOOP_DEFAULT_STEPS;
    }
    if (config->dt_sec <= 0.0)
    {
        config->dt_sec = CONTROL_LOOP_DEFAULT_DT_SEC;
    }
    if (config->control_limit <= 0.0)
    {
        config->control_limit = CONTROL_LOOP_DEFAULT_CONTROL_LIMIT;
    }
}

static void reset_inputs(controller_inputs_t *inputs)
{
    inputs->measurement = 0.0;
    inputs->has_measurement = 0;
    inputs->has_orchestrator_heartbeat = 0;
    inputs->has_plant_heartbeat = 0;
}

static can_if_status_t receive_controller_inputs(
    can_if_handle_t *handle,
    controller_inputs_t *inputs,
    uint32_t timeout_ms,
    uint32_t step)
{
    uint32_t start_ms = can_if_get_time_ms();

    while ((can_if_get_time_ms() - start_ms) <= timeout_ms)
    {
        can_if_frame_t frame;
        can_if_status_t status = can_if_receive(handle, &frame, timeout_ms);
        if (status == CAN_IF_TIMEOUT)
        {
            continue;
        }
        if (status != CAN_IF_OK)
        {
            return status;
        }

        if (frame.id == CAN_ID_SETPOINT_CMD)
        {
            if (can_codec_unpack_setpoint(&frame, &inputs->setpoint) == CAN_CODEC_OK)
            {
                inputs->has_setpoint = 1;
            }
        }
        else if (frame.id == CAN_ID_MEASUREMENT_FB)
        {
            if (can_codec_unpack_measurement(&frame, &inputs->measurement) == CAN_CODEC_OK)
            {
                inputs->has_measurement = 1;
            }
        }
        else if (frame.id == CAN_ID_HEARTBEAT)
        {
            uint8_t node_id = 0U;
            uint8_t alive_counter = 0U;
            if (can_codec_unpack_heartbeat(&frame, &node_id, &alive_counter) == CAN_CODEC_OK)
            {
                if (node_id == CAN_NODE_ID_ORCH)
                {
                    inputs->orchestrator_alive_counter = alive_counter;
                    inputs->has_orchestrator_heartbeat = 1;
                }
                else if (node_id == CAN_NODE_ID_PLANT)
                {
                    inputs->plant_alive_counter = alive_counter;
                    inputs->has_plant_heartbeat = 1;
                }
            }
        }

        if (inputs->has_setpoint && inputs->has_measurement && inputs->has_orchestrator_heartbeat)
        {
            char message[256];
            (void)snprintf(
                message,
                sizeof(message),
                "received setpoint=%.6f measurement=%.6f orch_alive=%u plant_alive=%u",
                inputs->setpoint,
                inputs->measurement,
                (unsigned int)inputs->orchestrator_alive_counter,
                (unsigned int)inputs->plant_alive_counter);
            log_stdout_line("RECV_OK", step, message);
            return CAN_IF_OK;
        }
    }

    {
        char message[256];
        (void)snprintf(
            message,
            sizeof(message),
            "timeout waiting inputs has_setpoint=%d has_measurement=%d has_orch_hb=%d has_plant_hb=%d last_orch_alive=%u last_plant_alive=%u",
            inputs->has_setpoint,
            inputs->has_measurement,
            inputs->has_orchestrator_heartbeat,
            inputs->has_plant_heartbeat,
            (unsigned int)inputs->orchestrator_alive_counter,
            (unsigned int)inputs->plant_alive_counter);
        log_stderr_line("RECV_TIMEOUT", step, message);
    }
    return CAN_IF_TIMEOUT;
}

static can_if_status_t send_codec_frame(can_if_handle_t *handle, const can_if_frame_t *frame)
{
    can_if_frame_t frame_to_send;

    if ((handle == 0) || (frame == 0))
    {
        return CAN_IF_INVALID_ARG;
    }
    frame_to_send = *frame;
    frame_to_send.timestamp_ms = can_if_get_time_ms();
    return can_if_send(handle, &frame_to_send);
}

static int send_controller_outputs(
    can_if_handle_t *handle,
    double control_output,
    uint8_t alive_counter,
    uint8_t state_code,
    uint8_t trial_active)
{
    can_if_frame_t frame;
    can_if_status_t status;
    can_codec_status_t codec_status;
    uint32_t timestamp_ms = can_if_get_time_ms();

    codec_status = can_codec_pack_control_output(control_output, &frame);
    if (codec_status != CAN_CODEC_OK)
    {
        fprintf(stderr, "can_codec_pack_control_output failed: %d\n", (int)codec_status);
        return 1;
    }
    status = send_codec_frame(handle, &frame);
    if (status != CAN_IF_OK)
    {
        return report_can_error("control_output send", status, handle);
    }

    codec_status = can_codec_pack_status(state_code, 0U, trial_active, timestamp_ms, &frame);
    if (codec_status != CAN_CODEC_OK)
    {
        fprintf(stderr, "can_codec_pack_status failed: %d\n", (int)codec_status);
        return 1;
    }
    status = send_codec_frame(handle, &frame);
    if (status != CAN_IF_OK)
    {
        return report_can_error("status send", status, handle);
    }

    codec_status = can_codec_pack_heartbeat(CAN_NODE_ID_CONTROLLER, alive_counter, &frame);
    if (codec_status != CAN_CODEC_OK)
    {
        fprintf(stderr, "can_codec_pack_heartbeat failed: %d\n", (int)codec_status);
        return 1;
    }
    status = send_codec_frame(handle, &frame);
    if (status != CAN_IF_OK)
    {
        return report_can_error("heartbeat send", status, handle);
    }

    return 0;
}

int control_loop_run_once(void)
{
#if defined(USE_VECTOR_XL)
    control_loop_config_t loop_config;
    can_if_config_t can_config;
    can_if_handle_t *handle = 0;
    can_if_status_t status;
    controller_inputs_t inputs;
    pid_controller_state_t pid_state;
    uint32_t step;
    uint8_t alive_counter = 0U;
    int exit_code = 0;

    load_control_loop_config(&loop_config);
    memset(&inputs, 0, sizeof(inputs));
    memset(&pid_state, 0, sizeof(pid_state));
    memset(&can_config, 0, sizeof(can_config));
    can_config.channel_index = loop_config.channel_index;
    can_config.bitrate = loop_config.bitrate;
    can_config.rx_timeout_ms = loop_config.rx_timeout_ms;

    status = can_if_init(&handle, &can_config);
    if (status != CAN_IF_OK)
    {
        return report_can_error("can_if_init", status, handle);
    }

    status = can_if_open(handle);
    if (status != CAN_IF_OK)
    {
        exit_code = report_can_error("can_if_open", status, handle);
        (void)can_if_deinit(handle);
        return exit_code;
    }

    {
        char startup_message[256];
        (void)snprintf(
            startup_message,
            sizeof(startup_message),
            "channel=%u bitrate=%u rx_timeout_ms=%u steps=%u dt_sec=%.6f control_limit=%.6f",
            (unsigned int)loop_config.channel_index,
            (unsigned int)loop_config.bitrate,
            (unsigned int)loop_config.rx_timeout_ms,
            (unsigned int)loop_config.steps,
            loop_config.dt_sec,
            loop_config.control_limit);
        log_stdout_line("START", 0U, startup_message);
    }

    for (step = 0U; step < loop_config.steps; ++step)
    {
        double control_output;
        char step_message[256];

        reset_inputs(&inputs);
        (void)snprintf(
            step_message,
            sizeof(step_message),
            "waiting for setpoint/measurement/heartbeat timeout_ms=%u",
            (unsigned int)(loop_config.rx_timeout_ms * 5U));
        log_stdout_line("WAIT_INPUT", step, step_message);
        status = receive_controller_inputs(handle, &inputs, loop_config.rx_timeout_ms * 5U, step);
        if (status != CAN_IF_OK)
        {
            exit_code = report_can_error("receive_controller_inputs", status, handle);
            break;
        }
        if (!inputs.has_setpoint || !inputs.has_measurement)
        {
            fprintf(stderr, "controller step %lu missing required inputs\n", (unsigned long)step);
            exit_code = 1;
            break;
        }

        control_output = pid_controller_step(&pid_state, inputs.setpoint, inputs.measurement, loop_config.dt_sec);
        if (control_output > loop_config.control_limit)
        {
            control_output = loop_config.control_limit;
        }
        if (control_output < -loop_config.control_limit)
        {
            control_output = -loop_config.control_limit;
        }

        (void)snprintf(
            step_message,
            sizeof(step_message),
            "setpoint=%.6f measurement=%.6f control_output=%.6f alive_counter=%u",
            inputs.setpoint,
            inputs.measurement,
            control_output,
            (unsigned int)alive_counter);
        log_stdout_line("STEP", step, step_message);

        exit_code = send_controller_outputs(
            handle,
            control_output,
            alive_counter,
            (step + 1U < loop_config.steps) ? CAN_STATE_RUNNING : CAN_STATE_FINISHED,
            (step + 1U < loop_config.steps) ? 1U : 0U);
        if (exit_code != 0)
        {
            break;
        }
        log_stdout_line("SEND_OK", step, "control_output/status/heartbeat sent");
        alive_counter = (uint8_t)((alive_counter + 1U) & 0xFFU);
    }

    if (exit_code == 0)
    {
        printf("Vector XL controller roundtrip succeeded on channel %u at %u bps for %u steps.\n",
               (unsigned int)loop_config.channel_index,
               (unsigned int)loop_config.bitrate,
               (unsigned int)loop_config.steps);
    }

    (void)can_if_close(handle);
    (void)can_if_deinit(handle);
    return exit_code;
#else
    return 0;
#endif
}
