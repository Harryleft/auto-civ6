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


def load_config(environ: Mapping[str, str] | None = None) -> AgentConfig:
    """读取并校验凭据；缺任何一个都直接失败，不返回半可用配置。"""

    source = os.environ if environ is None else environ
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
