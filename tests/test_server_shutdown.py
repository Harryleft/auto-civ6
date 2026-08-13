from __future__ import annotations

import signal

from civ_mcp import server


def test_main_treats_keyboard_interrupt_as_normal_shutdown(monkeypatch) -> None:
    monkeypatch.delenv("CIV_MCP_DISABLE_LUA", raising=False)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)

    def interrupt(*, transport: str) -> None:
        assert transport == "stdio"
        raise KeyboardInterrupt

    monkeypatch.setattr(server.mcp, "run", interrupt)

    assert server.main() is None
