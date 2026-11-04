"""Execution lifecycle state management and transition guards."""

from app.models.execution import NodeStatus, RunStatus

TERMINAL_RUN_STATES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.PARTIALLY_COMPLETED.value,
    RunStatus.CANCELLED.value,
}

VALID_RUN_TRANSITIONS: dict[str, set[str]] = {
    RunStatus.PENDING.value: {
        RunStatus.QUEUED.value,
        RunStatus.RUNNING.value,
        RunStatus.CANCEL_REQUESTED.value,
        RunStatus.CANCELLED.value,
        RunStatus.FAILED.value,
    },
    RunStatus.QUEUED.value: {
        RunStatus.RUNNING.value,
        RunStatus.RECOVERING.value,
        RunStatus.CANCEL_REQUESTED.value,
        RunStatus.CANCELLED.value,
        RunStatus.FAILED.value,
    },
    RunStatus.RUNNING.value: {
        RunStatus.COMPLETED.value,
        RunStatus.PARTIALLY_COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.RECOVERING.value,
        RunStatus.CANCEL_REQUESTED.value,
        RunStatus.CANCELLED.value,
    },
    RunStatus.RECOVERING.value: {
        RunStatus.QUEUED.value,
        RunStatus.RUNNING.value,
        RunStatus.COMPLETED.value,
        RunStatus.PARTIALLY_COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    },
    RunStatus.CANCEL_REQUESTED.value: {
        RunStatus.CANCELLED.value,
        RunStatus.FAILED.value,
        RunStatus.COMPLETED.value,
        RunStatus.PARTIALLY_COMPLETED.value,
    },
    RunStatus.COMPLETED.value: set(),
    RunStatus.PARTIALLY_COMPLETED.value: set(),
    RunStatus.FAILED.value: set(),
    RunStatus.CANCELLED.value: set(),
}


def can_transition_run(current_status: str, new_status: str) -> bool:
    """Check if a WorkflowRun state transition is permitted."""
    current_upper = current_status.upper()
    new_upper = new_status.upper()
    allowed = VALID_RUN_TRANSITIONS.get(current_upper, set())
    return new_upper in allowed


def is_terminal_run_state(status_str: str) -> bool:
    """Return True if the run status is terminal."""
    return status_str.upper() in TERMINAL_RUN_STATES

