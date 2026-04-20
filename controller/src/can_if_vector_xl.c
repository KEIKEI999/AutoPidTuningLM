#include "can_if.h"

#if defined(USE_VECTOR_XL)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include "vxlapi.h"

struct can_if_handle_tag
{
    can_if_config_t config;
    XLportHandle port_handle;
    XLaccess channel_mask;
    XLaccess permission_mask;
    XLstatus last_status;
    int is_open;
};

static can_if_status_t vector_status_to_can(XLstatus status);

static int channel_supports_can(const XLchannelConfig *channel)
{
    if (channel == 0)
    {
        return 0;
    }
    if ((channel->connectedBusType & XL_BUS_TYPE_CAN) != 0U)
    {
        return 1;
    }
    if ((channel->channelBusCapabilities & XL_BUS_TYPE_CAN) != 0U)
    {
        return 1;
    }
    if ((channel->channelBusActiveCapabilities & XL_BUS_TYPE_CAN) != 0U)
    {
        return 1;
    }
    return 0;
}

static can_if_status_t resolve_channel_mask(can_if_handle_t *handle)
{
    unsigned int hw_type = 0U;
    unsigned int hw_index = 0U;
    unsigned int hw_channel = 0U;
    XLdriverConfig driver_config;
    XLstatus status;
    unsigned int available_ordinal = 0U;
    unsigned int i;

    status = xlGetApplConfig("AutoTuningLM",
                             handle->config.channel_index,
                             &hw_type,
                             &hw_index,
                             &hw_channel,
                             XL_BUS_TYPE_CAN);
    if (status == XL_SUCCESS)
    {
        handle->channel_mask = xlGetChannelMask((int)hw_type, (int)hw_index, (int)hw_channel);
        handle->permission_mask = handle->channel_mask;
        if (handle->channel_mask != 0U)
        {
            handle->last_status = XL_SUCCESS;
            return CAN_IF_OK;
        }
    }

    memset(&driver_config, 0, sizeof(driver_config));
    status = xlGetDriverConfig(&driver_config);
    if (status != XL_SUCCESS)
    {
        handle->last_status = status;
        return vector_status_to_can(status);
    }

    for (i = 0U; (i < driver_config.channelCount) && (i < XL_CONFIG_MAX_CHANNELS); ++i)
    {
        const XLchannelConfig *channel = &driver_config.channel[i];
        XLaccess mask = xlGetChannelMask((int)channel->hwType, (int)channel->hwIndex, (int)channel->hwChannel);
        if ((mask == 0U) || !channel_supports_can(channel))
        {
            continue;
        }
        if (available_ordinal == handle->config.channel_index)
        {
            handle->channel_mask = mask;
            handle->permission_mask = handle->channel_mask;
            handle->last_status = XL_SUCCESS;
            return CAN_IF_OK;
        }
        ++available_ordinal;
    }

    available_ordinal = 0U;
    for (i = 0U; (i < driver_config.channelCount) && (i < XL_CONFIG_MAX_CHANNELS); ++i)
    {
        const XLchannelConfig *channel = &driver_config.channel[i];
        XLaccess mask = xlGetChannelMask((int)channel->hwType, (int)channel->hwIndex, (int)channel->hwChannel);
        if (mask == 0U)
        {
            continue;
        }
        if (available_ordinal == handle->config.channel_index)
        {
            handle->channel_mask = mask;
            handle->permission_mask = handle->channel_mask;
            handle->last_status = XL_SUCCESS;
            return CAN_IF_OK;
        }
        ++available_ordinal;
    }

    handle->last_status = XL_ERR_HW_NOT_PRESENT;
    return CAN_IF_HW_ERROR;
}

static can_if_status_t vector_status_to_can(XLstatus status)
{
    if (status == XL_SUCCESS)
    {
        return CAN_IF_OK;
    }
    if (status == XL_ERR_QUEUE_IS_EMPTY)
    {
        return CAN_IF_TIMEOUT;
    }
    return CAN_IF_HW_ERROR;
}

can_if_status_t can_if_init(can_if_handle_t **handle, const can_if_config_t *config)
{
    if ((handle == 0) || (config == 0))
    {
        return CAN_IF_INVALID_ARG;
    }
    *handle = (can_if_handle_t *)calloc(1, sizeof(can_if_handle_t));
    if (*handle == 0)
    {
        return CAN_IF_ERROR;
    }
    (*handle)->config = *config;
    (*handle)->port_handle = XL_INVALID_PORTHANDLE;
    (*handle)->last_status = XL_SUCCESS;
    return CAN_IF_OK;
}

can_if_status_t can_if_open(can_if_handle_t *handle)
{
    XLstatus status;

    if (handle == 0)
    {
        return CAN_IF_INVALID_ARG;
    }

    status = xlOpenDriver();
    if (status != XL_SUCCESS)
    {
        handle->last_status = status;
        return vector_status_to_can(status);
    }

    if (resolve_channel_mask(handle) != CAN_IF_OK)
    {
        return CAN_IF_HW_ERROR;
    }

    status = xlOpenPort(&handle->port_handle,
                        "AutoTuningLM",
                        handle->channel_mask,
                        &handle->permission_mask,
                        1024U,
                        XL_INTERFACE_VERSION,
                        XL_BUS_TYPE_CAN);
    if (status != XL_SUCCESS)
    {
        handle->last_status = status;
        return vector_status_to_can(status);
    }

    status = xlCanSetChannelBitrate(handle->port_handle, handle->permission_mask, handle->config.bitrate);
    if (status != XL_SUCCESS)
    {
        handle->last_status = status;
        return vector_status_to_can(status);
    }

    status = xlActivateChannel(handle->port_handle,
                               handle->channel_mask,
                               XL_BUS_TYPE_CAN,
                               XL_ACTIVATE_RESET_CLOCK);
    handle->last_status = status;
    if (status != XL_SUCCESS)
    {
        return vector_status_to_can(status);
    }

    handle->is_open = 1;
    return CAN_IF_OK;
}

can_if_status_t can_if_close(can_if_handle_t *handle)
{
    if (handle == 0)
    {
        return CAN_IF_INVALID_ARG;
    }
    if (!handle->is_open)
    {
        return CAN_IF_OK;
    }
    (void)xlDeactivateChannel(handle->port_handle, handle->channel_mask);
    (void)xlClosePort(handle->port_handle);
    (void)xlCloseDriver();
    handle->port_handle = XL_INVALID_PORTHANDLE;
    handle->is_open = 0;
    return CAN_IF_OK;
}

can_if_status_t can_if_deinit(can_if_handle_t *handle)
{
    if (handle == 0)
    {
        return CAN_IF_INVALID_ARG;
    }
    (void)can_if_close(handle);
    free(handle);
    return CAN_IF_OK;
}

can_if_status_t can_if_send(can_if_handle_t *handle, const can_if_frame_t *frame)
{
    unsigned int message_count = 1U;
    XLevent event;
    XLstatus status;

    if ((handle == 0) || (frame == 0))
    {
        return CAN_IF_INVALID_ARG;
    }
    if (!handle->is_open)
    {
        return CAN_IF_NOT_OPEN;
    }

    memset(&event, 0, sizeof(event));
    event.tag = XL_TRANSMIT_MSG;
    event.tagData.msg.id = frame->id;
    if (frame->id_type == CAN_IF_ID_EXTENDED)
    {
        event.tagData.msg.flags |= XL_CAN_EXT_MSG_ID;
    }
    event.tagData.msg.dlc = frame->dlc;
    memcpy(event.tagData.msg.data, frame->data, frame->dlc);

    status = xlCanTransmit(handle->port_handle, handle->channel_mask, &message_count, &event);
    handle->last_status = status;
    return vector_status_to_can(status);
}

can_if_status_t can_if_receive(can_if_handle_t *handle, can_if_frame_t *frame, uint32_t timeout_ms)
{
    DWORD start_ms;
    XLstatus status = XL_ERR_QUEUE_IS_EMPTY;
    unsigned int event_count = 1U;
    XLevent event;

    if ((handle == 0) || (frame == 0))
    {
        return CAN_IF_INVALID_ARG;
    }
    if (!handle->is_open)
    {
        return CAN_IF_NOT_OPEN;
    }

    start_ms = GetTickCount();
    while ((GetTickCount() - start_ms) <= timeout_ms)
    {
        memset(&event, 0, sizeof(event));
        event_count = 1U;
        status = xlReceive(handle->port_handle, &event_count, &event);
        if (status == XL_SUCCESS && event.tag == XL_RECEIVE_MSG)
        {
            memset(frame, 0, sizeof(*frame));
            frame->id = (uint32_t)(event.tagData.msg.id & 0x1FFFFFFFUL);
            frame->id_type = ((event.tagData.msg.flags & XL_CAN_EXT_MSG_ID) != 0U) ? CAN_IF_ID_EXTENDED : CAN_IF_ID_STANDARD;
            frame->dlc = (uint8_t)event.tagData.msg.dlc;
            memcpy(frame->data, event.tagData.msg.data, frame->dlc);
            frame->timestamp_ms = (uint32_t)(event.timeStamp / 1000000ULL);
            handle->last_status = status;
            return CAN_IF_OK;
        }
        if (status == XL_SUCCESS)
        {
            continue;
        }
        if (status != XL_ERR_QUEUE_IS_EMPTY)
        {
            handle->last_status = status;
            return vector_status_to_can(status);
        }
        Sleep(1U);
    }

    handle->last_status = status;
    return CAN_IF_TIMEOUT;
}

can_if_status_t can_if_get_last_error(can_if_handle_t *handle, int32_t *error_code)
{
    if ((handle == 0) || (error_code == 0))
    {
        return CAN_IF_INVALID_ARG;
    }
    *error_code = (int32_t)handle->last_status;
    return CAN_IF_OK;
}

uint32_t can_if_get_time_ms(void)
{
    return (uint32_t)GetTickCount();
}

#else

struct can_if_handle_tag
{
    int32_t last_error;
};

can_if_status_t can_if_init(can_if_handle_t **handle, const can_if_config_t *config)
{
    if ((handle == 0) || (config == 0))
    {
        return CAN_IF_INVALID_ARG;
    }
    *handle = (can_if_handle_t *)0;
    return CAN_IF_OK;
}

can_if_status_t can_if_open(can_if_handle_t *handle)
{
    (void)handle;
    return CAN_IF_HW_ERROR;
}

can_if_status_t can_if_close(can_if_handle_t *handle)
{
    (void)handle;
    return CAN_IF_OK;
}

can_if_status_t can_if_deinit(can_if_handle_t *handle)
{
    (void)handle;
    return CAN_IF_OK;
}

can_if_status_t can_if_send(can_if_handle_t *handle, const can_if_frame_t *frame)
{
    (void)handle;
    (void)frame;
    return CAN_IF_HW_ERROR;
}

can_if_status_t can_if_receive(can_if_handle_t *handle, can_if_frame_t *frame, uint32_t timeout_ms)
{
    (void)handle;
    (void)frame;
    (void)timeout_ms;
    return CAN_IF_HW_ERROR;
}

can_if_status_t can_if_get_last_error(can_if_handle_t *handle, int32_t *error_code)
{
    (void)handle;
    if (error_code != 0)
    {
        *error_code = -5;
    }
    return CAN_IF_OK;
}

uint32_t can_if_get_time_ms(void)
{
    return 0U;
}

#endif
