"""Durable operation state machine: the restart point for a run.

Adapted from Pi's ``harness.md`` §0.3 / §3. A durable *operation* is one accepted
unit of work (here: one ``Agent.prompt`` run). Its **total current state** is
persisted after every transition at the bound address
``operation_state(operation_id)``. Recovery reads that state and resumes at the
responsible procedure; it never replays a journal or infers position from what is
missing.

The reason this exists: an agent can be killed between any two steps. Without a
restart point, a resumed run either repeats settled tool effects or loses the
turn. Pi's answer is three rules:

1. **Total state.** Replace the whole state each transition; never patch it.
2. **Intent then settlement.** Before an uncertain external effect, durably
   record the intent (with the ids the outcome will use). After, settle it.
3. **Idempotent hooks.** Hook side effects must be idempotent; a crash before the
   consuming transaction may rerun the hook.

This module keeps the mechanism small: a status enum, a serializable state, and
an :class:`Operation` that drives transitions through a :class:`Store`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tm.ai.types import now_ms
from tm.core.store import Store, operation_result, operation_state


class OperationStatus(StrEnum):
    """Durable lifecycle of one operation.

    ``open`` — accepted, work may proceed.
    ``effect_pending`` — an external effect (a tool call) is in flight. A crash
        here is the one genuinely uncertain window.
    ``completed`` — settled successfully.
    ``failed`` — settled as an error.
    ``aborted`` — settled because the user aborted.
    """

    OPEN = "open"
    EFFECT_PENDING = "effect_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


TERMINAL = (OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.ABORTED)


class PendingEffect(BaseModel):
    """The intent record written before an uncertain external effect."""

    tool_name: str
    call_id: str
    arguments: dict = Field(default_factory=dict)
    replay_safe: bool = False


class OperationState(BaseModel):
    """The total, serializable state of an operation.

    Every field is either a scalar or a small bounded structure. No history is
    retained: the previous state is replaced, not appended to.
    """

    operation_id: str
    kind: str = "run"
    status: OperationStatus = OperationStatus.OPEN
    turn: int = 0
    message_count: int = 0
    pending: PendingEffect | None = None
    started_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)


class Operation:
    """Drives one durable operation through the state machine over a ``Store``."""

    def __init__(self, operation_id: str, store: Store) -> None:
        self.operation_id = operation_id
        self.store = store
        self._address = operation_state(operation_id)
        self._result_address = operation_result(operation_id)
        self.state = self._load()

    # -- lifecycle --------------------------------------------------------
    @classmethod
    def accept(cls, operation_id: str, store: Store, *, kind: str = "run") -> Operation:
        """Durably accept a new operation and return it in the ``open`` state."""
        operation = cls.__new__(cls)
        operation.operation_id = operation_id
        operation.store = store
        operation._address = operation_state(operation_id)
        operation._result_address = operation_result(operation_id)
        operation.state = OperationState(operation_id=operation_id, kind=kind)
        operation._persist()
        return operation

    def _load(self) -> OperationState:
        raw = self.store.get_value(self._address)
        if raw is None:
            raise OperationNotFound(f"no durable operation: {self.operation_id}")
        return OperationState.model_validate(raw)

    def _persist(self) -> None:
        self.state.updated_at = now_ms()
        self.store.set_value(self._address, self.state.model_dump(mode="json"))

    def set_turn(self, turn: int) -> None:
        """Record the current turn while staying in the open state."""
        if self.state.turn != turn:
            self.state.turn = turn
            self._persist()

    @staticmethod
    def pending(store: Store) -> list[OperationState]:
        """Operations left in ``effect_pending``: interrupted mid-effect.

        Recovery must reconcile these before starting new work, since a tool may
        or may not have run.
        """
        states: list[OperationState] = []
        for _address, raw in store.scan_values(operation_state("").namespace):
            if not isinstance(raw, dict):
                continue
            state = OperationState.model_validate(raw)
            if state.status is OperationStatus.EFFECT_PENDING:
                states.append(state)
        return states

    # -- transitions ------------------------------------------------------
    def begin_effect(self, effect: PendingEffect, *, turn: int) -> None:
        """Record intent before an uncertain external effect.

        Recovery that finds ``effect_pending`` knows a tool may or may not have
        run. If ``replay_safe`` it re-executes with the persisted arguments;
        otherwise it reconciles without re-running.
        """
        self.state.status = OperationStatus.EFFECT_PENDING
        self.state.pending = effect
        self.state.turn = turn
        self._persist()

    def settle_effect(self, *, message_count: int) -> None:
        """Settle a just-completed effect and return to the open state."""
        self.state.pending = None
        self.state.status = OperationStatus.OPEN
        self.state.message_count = message_count
        self._persist()

    def settle(self, status: OperationStatus, *, message_count: int) -> None:
        """Finish the operation: clear operation-owned state, write the result.

        The result is an immutable terminal record. Operation-owned values are
        removed in the same step so a later restart sees only conversation and
        the handful of session-level values.
        """
        if status not in TERMINAL:
            raise ValueError(f"settle requires a terminal status, got {status}")
        self.state.status = status
        self.state.pending = None
        self.state.message_count = message_count
        self._persist()
        self.store.set_value(
            self._result_address,
            {
                "operationId": self.operation_id,
                "kind": self.state.kind,
                "status": status.value,
                "turn": self.state.turn,
                "messageCount": message_count,
                "startedAt": self.state.started_at,
                "endedAt": now_ms(),
            },
        )
        self.store.delete_value(self._address)

    @property
    def is_terminal(self) -> bool:
        return self.state.status in TERMINAL

    @property
    def needs_recovery(self) -> bool:
        return self.state.status == OperationStatus.EFFECT_PENDING

    def result(self) -> dict[str, Any] | None:
        return self.store.get_value(self._result_address)


class OperationNotFound(LookupError):
    """Raised when loading an operation that has no durable state."""


__all__ = [
    "Operation",
    "OperationNotFound",
    "OperationState",
    "OperationStatus",
    "PendingEffect",
    "TERMINAL",
]
