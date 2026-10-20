"""在 Windows 原生 MCP 会话中验证桌面应用不随客户端退出而结束。"""

import asyncio
import ctypes
from ctypes import wintypes
from datetime import timedelta
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from civ_mcp import game_launcher


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='仅验证 Windows 原生进程生命周期')


@pytest.mark.parametrize('failure', [False, True])
def test_desktop_launch_quotes_arguments_and_releases_thread_com(monkeypatch, failure):
    calls = []

    def execute(*args):
        calls.append(('execute', args))
        if failure:
            raise RuntimeError('desktop unavailable')

    shell = SimpleNamespace(ShellExecute=execute)
    desktop = SimpleNamespace(Document=SimpleNamespace(Application=shell))
    windows = SimpleNamespace(FindWindowSW=lambda *args: calls.append(('find', args)) or desktop)
    client = SimpleNamespace(Dispatch=lambda name: calls.append(('dispatch', name)) or windows)
    monkeypatch.setitem(sys.modules, 'pythoncom', SimpleNamespace(
        CoInitialize=lambda: calls.append(('init',)),
        CoUninitialize=lambda: calls.append(('uninit',)),
    ))
    monkeypatch.setitem(sys.modules, 'win32com', SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, 'win32com.client', client)
    command = [r'C:\游戏 路径\steam.exe', '-applaunch', '289070', '含 空格']
    if failure:
        with pytest.raises(OSError, match='desktop unavailable'):
            game_launcher._launch_windows_application(command)
    else:
        game_launcher._launch_windows_application(command)
    assert calls == [
        ('init',), ('dispatch', '{9BA05972-F6A8-11CF-A442-00A0C90A8F39}'),
        ('find', (0, 0, 8, 0, 1)),
        ('execute', (command[0], subprocess.list2cmdline(command[1:]), '', 'open', 1)),
        ('uninit',),
    ]


def test_desktop_process_survives_real_mcp_stdio_shutdown(tmp_path):
    get_shell_window = ctypes.WinDLL('user32').GetShellWindow
    get_shell_window.restype = wintypes.HWND
    if not get_shell_window():
        pytest.skip('当前 Windows 会话没有交互式桌面，不能验证桌面进程启动')

    import win32api
    import win32con
    import win32event
    import win32process

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    child = tmp_path / 'idle_child.py'
    receipt = tmp_path / 'started.json'
    child.write_text(
        'import json, os, pathlib, sys, time\n'
        'pathlib.Path(sys.argv[1]).write_text(json.dumps({"pid": os.getpid()}))\n'
        'time.sleep(60)\n', encoding='utf-8',
    )
    server = tmp_path / 'idle_server.py'
    server.write_text(
        'import json, pathlib, sys, time\n'
        'from mcp.server.fastmcp import FastMCP\n'
        'from civ_mcp.game_launcher import _launch_windows_application\n'
        'mcp = FastMCP("lifetime regression")\n'
        '@mcp.tool()\n'
        'def launch() -> str:\n'
        f'    receipt = pathlib.Path({str(receipt)!r})\n'
        '    executable = str(pathlib.Path(sys._base_executable).with_name("pythonw.exe"))\n'
        f'    _launch_windows_application([executable, {str(child)!r}, str(receipt)])\n'
        '    for _ in range(100):\n'
        '        if receipt.exists():\n'
        '            return receipt.read_text()\n'
        '        time.sleep(.1)\n'
        '    raise RuntimeError("child did not start")\n'
        'mcp.run(transport="stdio")\n', encoding='utf-8',
    )
    # 句柄在会话关闭前打开，既能回收本测试子进程，也不会误认复用的 PID。
    handles = []

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=['-X', 'utf8', str(server)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                await session.initialize()
                result = await session.call_tool('launch')
                assert not result.isError, result.content[0].text
                pid = json.loads(result.content[0].text)['pid']
                handle = win32api.OpenProcess(
                    win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE, False, pid,
                )
                handles.append(handle)
        assert win32event.WaitForSingleObject(handle, 1000) == win32event.WAIT_TIMEOUT

    try:
        asyncio.run(scenario())
    finally:
        for handle in handles:
            try:
                if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT:
                    win32process.TerminateProcess(handle, 0)
            finally:
                win32api.CloseHandle(handle)
