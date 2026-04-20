#include "pid_controller.h"
#include "pid_params.h"

double pid_controller_step(pid_controller_state_t *state, double setpoint, double measurement, double dt_sec)
{
    double error = setpoint - measurement;
    double derivative = 0.0;
    if ((state == 0) || (dt_sec <= 0.0))
    {
        return 0.0;
    }
    state->integral += error * dt_sec;
    derivative = (error - state->prev_error) / dt_sec;
    state->prev_error = error;
    return (PID_KP * error) + (PID_KI * state->integral) + (PID_KD * derivative);
}

