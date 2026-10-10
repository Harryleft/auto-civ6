"""本地动作日志；只记录调用与原始结果，不推断执行是否成功。"""

import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4


class GameLogger:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".civ6-mcp" / "actions" / f"{uuid4().hex}.jsonl"

    async def log_tool_call(self, tool, params, result, duration_ms):
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "params": params,
            "result": result,
            "duration_ms": duration_ms,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
