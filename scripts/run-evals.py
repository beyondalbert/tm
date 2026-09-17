"""Run the offline eval scenarios and print a report.

The scenarios replay a deterministic faux provider, so this needs no API key and
no network. Exit code 0 means every scenario passed.

    python -m uv run python scripts/run-evals.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.evals.harness import format_report, run_scenario  # noqa: E402
from tests.evals.test_scenarios import SCENARIOS  # noqa: E402


async def main() -> int:
    results = [await run_scenario(scenario) for scenario in SCENARIOS]
    print(format_report(results))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
