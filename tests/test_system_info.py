from __future__ import annotations

from tm.tools.base import ToolContext
from tm.tools.system_info import SystemInfoParams, SystemInfoTool


async def test_system_info_reports_the_machine(tmp_path) -> None:
    result = await SystemInfoTool().execute(
        "1", SystemInfoParams(), ToolContext(cwd=tmp_path)
    )
    assert result.is_error is False
    text = result.text()
    assert "Environment:" in text
    assert "Python:" in text
    assert "Machine:" in text
