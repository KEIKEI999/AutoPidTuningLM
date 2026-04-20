#ifndef PID_CONTROLLER_H
#define PID_CONTROLLER_H

typedef struct
{
    double integral;
    double prev_error;
} pid_controller_state_t;

double pid_controller_step(pid_controller_state_t *state, double setpoint, double measurement, double dt_sec);

#endif /* PID_CONTROLLER_H */

