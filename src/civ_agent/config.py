"""环境与密钥装配（M03）。

两个模型集成各自只认一个环境变量：

- ``langchain_deepseek.ChatDeepSeek`` 读 ``DEEPSEEK_API_KEY``；
- ``langchain_typesafe.TypeSafeClassifier`` 读 ``TYPESAFE_API_KEY``。

本仓库本地只有 ``JEV_API_KEY``，所以启动时做**一次**映射
（``JEV_API_KEY`` → ``TYPESAFE_API_KEY``），之后完全交给官方集成。
方案 §6 的硬约束：不建自建 gateway、不直接用 ``typesafe-sdk``、不自己写 HTTP
client、不再包一层 provider abstraction。

缺 key 时 fail-fast，不做静默降级——没有 Jev 就没有正式认知路径。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
TYPESAFE_API_KEY_ENV = "TYPESAFE_API_KEY"
JEV_API_KEY_ENV = "JEV_API_KEY"

#: 按此顺序检查；左侧缺失时依次尝试右侧来源。
_KEY_SOURCES: dict[str, tuple[str, ...]] = {
    DEEPSEEK_API_KEY_ENV: (DEEPSEEK_API_KEY_ENV,),
    TYPESAFE_API_KEY_ENV: (TYPESAFE_API_KEY_ENV, JEV_API_KEY_ENV),
}


class MissingCredentialError(RuntimeError):
    """启动所需的环境变量缺失；消息必须说明缺哪个、怎么补。"""


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """一次运行的模型凭据与端点。"""

    deepseek_api_key: str = field(repr=False)
    typesafe_api_key: str = field(repr=False)
    #: 实际生效的来源，便于在日志里说明 key 是直接提供还是由 JEV_API_KEY 映射。
    typesafe_source: str = TYPESAFE_API_KEY_ENV

    def masked(self) -> dict[str, str]:
        """可安全打印的配置摘要，绝不回显 key 内容。"""

        return {
            DEEPSEEK_API_KEY_ENV: "set",
            TYPESAFE_API_KEY_ENV: f"set (from {self.typesafe_source})",
        }


def _clean(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _resolve(environ: Mapping[str, str], name: str) -> tuple[str, str]:
    """返回 (值, 来源)。全部空即抛出，并把候选来源写进消息。"""

    candidates = _KEY_SOURCES[name]
    for candidate in candidates:
        value = _clean(environ, candidate)
        if value:
            return value, candidate
    raise MissingCredentialError(
        f"缺少 {name}。请设置 {' 或 '.join(candidates)}"
        + (
            f"（本地已有 {JEV_API_KEY_ENV} 时会自动映射为 {TYPESAFE_API_KEY_ENV}，"
            "但两者都未读到非空值）。"
            if len(candidates) > 1
            else "。"
        )
    )


def shell_environ() -> dict[str, str]:
    """当前环境 + 登录 shell 里导出的凭据（不打印任何值）。

    ``uv run`` / pytest 等非交互进程不会 source ``~/.zshrc``，因此用户在 shell
    里 export 的 key 对脚本不可见。这里用登录 shell 取一次；取不到就退回当前
    环境，仍然由 :func:`load_config` fail-fast。

    只读取本模块声明的这几个变量，不把整个登录环境灌进当前进程。
    """

    merged = dict(os.environ)
    wanted = sorted({name for names in _KEY_SOURCES.values() for name in names})
    missing = [name for name in wanted if not _clean(merged, name)]
    if not missing:
        return merged

    import shutil
    import subprocess

    shell = os.environ.get("SHELL") or "/bin/zsh"
    if not os.path.exists(shell):
        shell = shutil.which("zsh") or shutil.which("bash") or ""
    if not shell:
        return merged

    # -l 走登录 shell，-i 让 .zshrc 生效。用唯一的开始/结束标记包住，避免
    # shell 自身或 rc 文件的输出混进值里。不用 f-string，避免 ${...} 与 {} 冲突。
    marker = "__CIV6_ENV__"
    names = " ".join(missing)
    script = (
        'printf "%s\\n" "' + marker + '"; '
        "for __n in " + names + "; do "
        'eval "__v=\\${$__n}"; printf "%s\\t%s\\n" "$__n" "$__v"; '
        "done; "
        'printf "%s\\n" "' + marker + '"'
    )
    try:
        completed = subprocess.run(
            [shell, "-lic", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return merged

    # 只解析两个标记之间的内容；其余（rc 文件的问候语等）一律忽略。
    payload: list[str] = []
    inside = False
    for line in completed.stdout.splitlines():
        if line.strip() == marker:
            inside = not inside
            continue
        if inside:
            payload.append(line)

    for line in payload:
        name, _, value = line.partition("\t")
        if name.strip() in wanted and value:
            merged[name.strip()] = value
    return merged


def load_config(environ: Mapping[str, str] | None = None) -> AgentConfig:
    """读取并校验凭据；缺任何一个都直接失败，不返回半可用配置。

    ``environ`` 为 ``None`` 时除了当前进程环境，还会尝试从登录 shell 里取
    （见 :func:`shell_environ`）。脚本经 ``uv run`` 启动时不会自动继承
    ``~/.zshrc`` 里 export 的变量，而用户正是把 key 放在那里。
    """

    source = shell_environ() if environ is None else environ
    missing = [
        name
        for name in _KEY_SOURCES
        if not any(_clean(source, candidate) for candidate in _KEY_SOURCES[name])
    ]
    if missing:
        details = "；".join(
            f"{name}（可来自 {' / '.join(_KEY_SOURCES[name])}）" for name in missing
        )
        raise MissingCredentialError(
            f"以下环境变量缺失，civ_agent 无法启动：{details}。"
            "设置后重试；本仓库不会在缺 key 时降级运行。"
        )
    deepseek_key, _ = _resolve(source, DEEPSEEK_API_KEY_ENV)
    typesafe_key, typesafe_source = _resolve(source, TYPESAFE_API_KEY_ENV)
    return AgentConfig(
        deepseek_api_key=deepseek_key,
        typesafe_api_key=typesafe_key,
        typesafe_source=typesafe_source,
    )
