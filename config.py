"""用户身份验证插件的配置模型（与 plugin.py 分离，便于维护）。

固定槽位 + 「用户数量控制」数字：该数字 = 生成并显示的用户槽位个数（填 N 就生成 N 个，上限
``USER_SLOTS``），每个槽位各自独立 QQ / 昵称 / 限制群聊群号 / 多行模板 / 启用开关。
顶层 ``OwnerAuthConfig`` 用 ``create_model`` 动态绑定 plugin + user1..userN。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import tomllib

from maibot_sdk import PluginConfigBase
from pydantic import Field, create_model

# 插件 / 配置版本号（plugin.py、_manifest.json 与本文件保持一致）。
PLUGIN_VERSION = "2.2.2"

# 生成的用户槽位数 USER_SLOTS = 已保存的 user_count —— 不写死上限、也不留多余空槽位
# （config.toml 里有几个用户就几个 [userN] 表，干净）。固定槽位（每用户独立多行模板、非动态列表）天生
# 要在配置模型里预先生成 N 个字段；N 在本模块导入时按磁盘上已保存的 user_count 算出。因此把「用户数量
# 控制」调大后，到插件管理处把本插件「禁用再启用」（或重启麦麦）触发重新导入，即按新数量生成槽位——无硬上限。


def _saved_user_count() -> int:
    """读磁盘 config.toml 已保存的 user_count（导入期用，决定生成多少槽位）；读不到/异常按 1。"""

    try:
        path = Path(__file__).resolve().parent / "config.toml"
        if not path.exists():
            return 1
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        plugin_section = data.get("plugin") if isinstance(data, dict) else None
        return max(1, int((plugin_section or {}).get("user_count", 1)))
    except Exception:  # noqa: BLE001 - 读不到就按 1，绝不让导入失败
        return 1


USER_SLOTS = max(_saved_user_count(), 1)

DEFAULT_REPLYER_TEMPLATE = (
    "回复「{relation}」时保持自然、熟悉且更上心的语气；需要直接称呼时使用「{relation}」，"
    "不要机械地在每句话重复身份，也不要为了表现亲近而牺牲事实判断、安全边界或当前任务。"
)

DEFAULT_PLANNER_TEMPLATE = (
    "规划涉及已验证的「{relation}」时，对其当前需求、状态和此前提过的细节额外上心；"
    "可以更主动地追问、分享或提供具体帮助，但仍保持事实判断、安全边界和独立判断。"
)


class GeneralSectionConfig(PluginConfigBase):
    """通用设置（位于「插件」页）。"""

    __ui_label__ = "通用设置"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    enabled: bool = Field(
        default=True,
        description="总开关，关闭后插件不再注入任何身份提示词",
        json_schema_extra={"label": "启用插件"},
    )
    user_count: int = Field(
        default=1,
        description="生成并显示几个用户槽位（无硬上限，按需自动生成；调大后重载一次即自动多生成槽位）；调小并保存后会清空超出的槽位",
        json_schema_extra={"label": "用户数量控制", "min": 1},
    )
    enable_private_inject: bool = Field(
        default=True,
        description="是否在私聊中也注入身份提示词",
        json_schema_extra={"label": "私聊中注入"},
    )
    enable_planner_inject: bool = Field(
        default=True,
        description="是否把可信身份属性和非空规划便签注入行动规划（Planner）；关闭后只保留 Replyer 身份认证",
        json_schema_extra={"label": "同时注入行动规划(planner)"},
    )
    log_result: bool = Field(
        default=True,
        description="是否在日志中记录每次注入结果",
        json_schema_extra={"label": "插件记录注入日志"},
    )
    log_level: Literal["INFO", "DEBUG", "WARNING"] = Field(
        default="INFO",
        description="插件自身注入日志的输出等级（错误日志不受此影响、始终输出）",
        json_schema_extra={"label": "日志显示等级"},
    )
    cache_ttl_seconds: int = Field(
        default=300,
        description="消息身份索引的会话不活跃清理时间（秒）；活跃会话中的单条消息不会按 TTL 中途掉标（10-3600）",
        json_schema_extra={"label": "身份索引会话有效期(秒)", "min": 10, "max": 3600},
    )
    config_version: str = Field(
        default=PLUGIN_VERSION,
        description="配置版本号（请勿手动修改）",
        json_schema_extra={"label": "配置版本", "disabled": True},
    )


class UserSlotConfig(PluginConfigBase):
    """单个用户槽位配置基类（每个用户拥有独立的 QQ / 昵称 / 限制群聊 / 提示词）。"""

    enabled: bool = Field(
        default=True,
        description="是否启用此用户（关闭只是临时停用、不清空该用户数据；QQ 为空时即使启用也会被跳过）",
        json_schema_extra={"label": "启用此用户"},
    )
    qq: str = Field(
        default="",
        description="该用户的 QQ 号，留空表示此槽位未配置",
        json_schema_extra={"label": "用户 QQ 号"},
    )
    nickname: str = Field(
        default="用户",
        description="对该身份的关系称呼，会替换模板中的 {relation}",
        json_schema_extra={"label": "用户昵称"},
    )
    group_id: str = Field(
        default="",
        description=(
            "限制该用户身份只在某个群生效：填群号则该用户仅在这个群被认作用户，"
            "留空则所有聊天流（含私聊）都生效。同一 QQ 可在不同槽位分别配全局与群专属，群专属优先。"
        ),
        json_schema_extra={"label": "限制群聊（群号）"},
    )
    planner_prompt_template: str = Field(
        default=DEFAULT_PLANNER_TEMPLATE,
        description=(
            "该用户专属的 Planner 规划便签（支持多行，留空则 Planner 只接收可信身份标识）。占位符："
            "{relation}=关系称呼，{display_name}=聊天显示名，{user_id}=已认证账号，"
            "{message}=该用户消息，{relations}=所有启用身份的关系称呼"
        ),
        json_schema_extra={"label": "Planner 规划便签"},
    )
    replyer_prompt_template: str = Field(
        default=DEFAULT_REPLYER_TEMPLATE,
        description=(
            "该用户专属的 Replyer 回复便签（支持多行，留空则 Replyer 只接收可信身份凭证）。占位符："
            "{relation}=关系称呼，{display_name}=聊天显示名，{user_id}=已认证账号，"
            "{message}=发言者本次消息，{relations}=所有启用身份的关系称呼"
        ),
        json_schema_extra={"label": "Replyer 回复便签"},
    )


def _make_slot_class(index: int) -> type[UserSlotConfig]:
    """为第 index 个用户槽位生成一个 UserSlotConfig 子类（仅 WebUI 展示元数据不同）。

    用 type() 生成 UserSlotConfig 的子类，字段全部继承自 UserSlotConfig（不动态新增字段，
    因此与手写 UserNConfig 等价、风险低）；只设置 __ui_label__/__ui_icon__/__ui_order__。
    """

    return type(
        f"User{index}Config",
        (UserSlotConfig,),
        {
            "__ui_label__": f"用户 {index}",
            "__ui_icon__": "user",
            "__ui_order__": 10 + index,
        },
    )


# 预生成 USER_SLOTS 个槽位类（user1..userN）。
SLOT_CLASSES: list[type[UserSlotConfig]] = [_make_slot_class(i) for i in range(1, USER_SLOTS + 1)]


# 顶层配置：plugin + user1..userN。用 create_model 动态绑定 N 个用户槽位字段。
# 区块名必须含 plugin：宿主的 extract_plugin_config_version 要求 [plugin].config_version 存在。
_OWNER_FIELD_DEFS: dict[str, Any] = {
    "plugin": (GeneralSectionConfig, Field(default_factory=GeneralSectionConfig)),
}
for _i, _slot_cls in enumerate(SLOT_CLASSES, start=1):
    _OWNER_FIELD_DEFS[f"user{_i}"] = (_slot_cls, Field(default_factory=_slot_cls))
OwnerAuthConfig = create_model(
    "OwnerAuthConfig",
    __base__=PluginConfigBase,
    **_OWNER_FIELD_DEFS,
)
OwnerAuthConfig.__ui_label__ = "用户身份验证"
