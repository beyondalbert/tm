# Evals

End-to-end scenarios for TM, adapted from Pi's `evals` package. Evals differ from
unit tests: they drive the whole agent loop and assert on the observable outcome
(messages, tool calls, final text) rather than on internal functions.

## Running

```bash
uv run pytest tests/evals -q          # offline, deterministic
uv run pytest tests/evals -q -m eval  # same, explicit marker
```

The offline scenarios use `FauxProvider`, which replays a scripted list of
assistant messages. No API key, network call, or paid token is required, so the
suite is fast and reproducible in CI.

## Layout

| File | Purpose |
|------|---------|
| `faux_provider.py` | Deterministic scripted provider. |
| `harness.py` | `EvalScenario`, `run_scenario`, `format_report`. |
| `test_scenarios.py` | The scenario catalog. |

## Adding a scenario

1. Append an `EvalScenario` to `SCENARIOS` in `test_scenarios.py`.
2. Give it a `script` of assistant messages to replay.
3. Set `expect` checks: `min_messages`, `tool_calls`, `final_text_contains`.

## Live evals

Scenarios marked `live` call a real provider and skip when no credentials are
present. Keep them out of the default run; they cost money and can flake.

## Why evals and not just unit tests

Unit tests prove a function matches its contract. Evals prove the *agent* still
accomplishes a task after a refactor moves where the logic lives. When the loop,
tool dispatch, or prompt assembly changes, evals are the regression net.
