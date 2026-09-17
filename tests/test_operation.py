"""Tests for the durable operation state machine (restart point)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tm.core.operation import (
    Operation,
    OperationNotFound,
    OperationStatus,
    PendingEffect,
)
from tm.core.store import Store, operation_result, operation_state


def test_accept_writes_open_state() -> None:
    store = Store()
    op = Operation.accept("op1", store)
    assert op.state.status is OperationStatus.OPEN
    assert not op.is_terminal
    assert store.get_value(operation_state("op1")) is not None


def test_load_unknown_operation_raises() -> None:
    store = Store()
    with pytest.raises(OperationNotFound):
        Operation("missing", store)


def test_state_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    store = Store.open(path)
    op = Operation.accept("op1", store)
    op.state.turn = 3
    op._persist()

    reopened = Operation("op1", Store.open(path))
    assert reopened.state.turn == 3
    assert reopened.state.operation_id == "op1"


def test_effect_intent_then_settlement() -> None:
    store = Store()
    op = Operation.accept("op1", store)
    effect = PendingEffect(tool_name="shell", call_id="c1", arguments={"command": "ls"})
    op.begin_effect(effect, turn=1)
    assert op.state.status is OperationStatus.EFFECT_PENDING
    assert op.needs_recovery
    assert op.state.pending is not None
    assert op.state.pending.tool_name == "shell"

    op.settle_effect(message_count=4)
    assert op.state.status is OperationStatus.OPEN
    assert op.state.pending is None
    assert not op.needs_recovery
    assert op.state.message_count == 4


def test_recovery_detects_interrupted_effect(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    store = Store.open(path)
    op = Operation.accept("op1", store)
    op.begin_effect(
        PendingEffect(tool_name="write", call_id="c1", replay_safe=False), turn=1
    )
    # Simulate a crash: reopen and inspect.
    recovered = Operation("op1", Store.open(path))
    assert recovered.needs_recovery
    assert recovered.state.pending is not None
    assert recovered.state.pending.replay_safe is False


def test_settle_writes_result_and_clears_state() -> None:
    store = Store()
    op = Operation.accept("op1", store)
    op.settle(OperationStatus.COMPLETED, message_count=6)
    assert store.get_value(operation_state("op1")) is None
    result = store.get_value(operation_result("op1"))
    assert result is not None
    assert result["status"] == "completed"
    assert result["messageCount"] == 6
    assert op.result() == result


def test_settle_rejects_non_terminal_status() -> None:
    store = Store()
    op = Operation.accept("op1", store)
    with pytest.raises(ValueError):
        op.settle(OperationStatus.OPEN, message_count=0)


def test_failed_and_aborted_are_terminal() -> None:
    store = Store()
    for status in (OperationStatus.FAILED, OperationStatus.ABORTED):
        op = Operation.accept(f"op-{status.value}", store)
        op.settle(status, message_count=0)
        assert op.is_terminal
