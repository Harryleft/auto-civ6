"""M03：环境装配、fail-fast 与官方集成的可导入性。

这里同时锁住方案 §6 的硬约束：Jev 必须走 ``langchain_typesafe`` 官方集成，
不得自建 gateway。
"""

from __future__ import annotations

import pytest

from civ_agent.config import (
    DEEPSEEK_API_KEY_ENV,
    JEV_API_KEY_ENV,
    TYPESAFE_API_KEY_ENV,
    AgentConfig,
    MissingCredentialError,
    load_config,
)


def _env(**values: str) -> dict[str, str]:
    return dict(values)


# ---------------------------------------------------------------------------
# 凭据装配
# ---------------------------------------------------------------------------


def test_load_config_reads_both_keys_when_present() -> None:
    config = load_config(
        _env(
            **{
                DEEPSEEK_API_KEY_ENV: "ds-key",
                TYPESAFE_API_KEY_ENV: "ts-key",
            }
        )
    )

    assert config.deepseek_api_key == "ds-key"
    assert config.typesafe_api_key == "ts-key"
    assert config.typesafe_source == TYPESAFE_API_KEY_ENV


def test_load_config_maps_jev_api_key_to_typesafe() -> None:
    """方案 §6：本地只有 JEV_API_KEY，启动时映射一次。"""

    config = load_config(
        _env(**{DEEPSEEK_API_KEY_ENV: "ds-key", JEV_API_KEY_ENV: "jev-key"})
    )

    assert config.typesafe_api_key == "jev-key"
    assert config.typesafe_source == JEV_API_KEY_ENV


def test_explicit_typesafe_key_wins_over_jev_alias() -> None:
    config = load_config(
        _env(
            **{
                DEEPSEEK_API_KEY_ENV: "ds-key",
                TYPESAFE_API_KEY_ENV: "ts-key",
                JEV_API_KEY_ENV: "jev-key",
            }
        )
    )

    assert config.typesafe_api_key == "ts-key"
    assert config.typesafe_source == TYPESAFE_API_KEY_ENV


def test_load_config_fails_fast_when_deepseek_key_missing() -> None:
    with pytest.raises(MissingCredentialError) as excinfo:
        load_config(_env(**{TYPESAFE_API_KEY_ENV: "ts-key"}))

    assert DEEPSEEK_API_KEY_ENV in str(excinfo.value)


def test_load_config_fails_fast_when_jev_key_missing() -> None:
    with pytest.raises(MissingCredentialError) as excinfo:
        load_config(_env(**{DEEPSEEK_API_KEY_ENV: "ds-key"}))

    assert TYPESAFE_API_KEY_ENV in str(excinfo.value)
    assert JEV_API_KEY_ENV in str(excinfo.value)


def test_load_config_lists_every_missing_key_at_once() -> None:
    """缺多个 key 时一次报全，避免反复试错。"""

    with pytest.raises(MissingCredentialError) as excinfo:
        load_config(_env())

    message = str(excinfo.value)
    assert DEEPSEEK_API_KEY_ENV in message
    assert TYPESAFE_API_KEY_ENV in message


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_values_count_as_missing(blank: str) -> None:
    with pytest.raises(MissingCredentialError):
        load_config(_env(**{DEEPSEEK_API_KEY_ENV: blank, JEV_API_KEY_ENV: blank}))


def test_blank_typesafe_key_falls_back_to_jev_alias() -> None:
    config = load_config(
        _env(
            **{
                DEEPSEEK_API_KEY_ENV: "ds-key",
                TYPESAFE_API_KEY_ENV: "   ",
                JEV_API_KEY_ENV: "jev-key",
            }
        )
    )

    assert config.typesafe_api_key == "jev-key"


def test_config_masked_never_echoes_key_material() -> None:
    config = load_config(
        _env(**{DEEPSEEK_API_KEY_ENV: "ds-secret", JEV_API_KEY_ENV: "jev-secret"})
    )

    summary = config.masked()
    rendered = repr(summary) + str(summary)
    assert "ds-secret" not in rendered
    assert "jev-secret" not in rendered
    assert summary[TYPESAFE_API_KEY_ENV] == f"set (from {JEV_API_KEY_ENV})"


def test_dataclass_repr_hides_keys() -> None:
    """dataclass 的默认 repr 会把密钥打进日志，必须屏蔽。"""

    config = AgentConfig(deepseek_api_key="ds-secret", typesafe_api_key="jev-secret")

    assert "ds-secret" not in repr(config)
    assert "jev-secret" not in repr(config)


# ---------------------------------------------------------------------------
# 官方集成可导入性（方案 §6 硬约束）
# ---------------------------------------------------------------------------


def test_typesafe_official_integration_exports_required_names() -> None:
    from langchain_typesafe import Choice, Noul, Score, TypeSafeClassifier

    assert TypeSafeClassifier is not None
    assert {Choice, Noul, Score}


def test_typesafe_classifier_requires_questions() -> None:
    """questions 是必填且至少一条；空配置必须在构造时失败而不是发出空请求。"""

    from langchain_typesafe import TypeSafeClassifier

    with pytest.raises(Exception):
        TypeSafeClassifier(questions={}, api_key="test-key")


def test_typesafe_classifier_accepts_the_three_question_kinds() -> None:
    from langchain_typesafe import Choice, Noul, Score, TypeSafeClassifier

    classifier = TypeSafeClassifier(
        questions={
            "expand": Noul(instructions="当前局面是否应当扩张？"),
            "priority": Choice(
                instructions="本回合的首要方向？",
                criteria={"military": "军事", "economy": "经济"},
            ),
            "risk": Score(
                instructions="当前开战风险有多高？",
                criteria=["极低", "低", "中", "高", "极高"],
            ),
        },
        api_key="test-key",
    )

    assert set(classifier.questions) == {"expand", "priority", "risk"}


def test_deepseek_integration_imports_from_official_package() -> None:
    from langchain_deepseek import ChatDeepSeek

    assert ChatDeepSeek is not None


def _executable_source(module: object) -> str:
    """只保留可执行语句，去掉注释与 docstring。

    否则"禁止 typesafe-sdk"这句约束本身出现在 docstring 里就会被误判。
    """

    import ast

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""  # 抹掉 docstring 内容
    return ast.unparse(tree)


def test_agent_config_does_not_depend_on_typesafe_sdk() -> None:
    """方案 §6：禁止直接使用 ``typesafe-sdk`` 或自建 HTTP client。"""

    import civ_agent.config as config

    source = _executable_source(config)
    assert "typesafe_sdk" not in source
    assert "typesafe-sdk" not in source
    assert "httpx" not in source
    assert "requests" not in source
