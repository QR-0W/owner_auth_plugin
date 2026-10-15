"""用户身份验证插件的配置模型（与 plugin.py 分离，便于维护）。

用户以对象列表保存，原生配置页直接增删列表项；列表长度即用户数量。
每项独立配置 QQ、关系称呼、群范围、两张便签和启用开关，保存后热更新。
"""

from __future__ import annotations

from typing import List, Literal

from maibot_sdk import PluginConfigBase
from pydantic import Field

# 插件版本与配置兼容版本分开，避免宿主版本重建先裁剪旧 userN 数据。
PLUGIN_VERSION = "2.3.0"
CONFIG_VERSION = "2.2.2"

DEFAULT_REPLYER_TEMPLATE = (
    "回复「{relation}」时保持自然、熟悉且更上心的语气；需要直接称呼时使用「{relation}」，"
    "不要机械地在每句话重复身份，也不要为了表现亲近而牺牲事实判断、安全边界或当前任务。"
)

DEFAULT_PLANNER_TEMPLATE = (
    "规划涉及已验证的「{relation}」时，对其当前需求、状态和此前提过的细节额外上心；"
    "可以更主动地追问、分享或提供具体帮助，但仍保持事实判断、安全边界和独立判断。"
)


class GeneralSectionConfig(PluginConfigBase):
    """插件通用设置。"""

    __ui_label__ = "通用设置"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    enabled: bool = Field(
        default=True,
        description="总开关，关闭后插件不再注入任何身份提示词",
        json_schema_extra={"label": "启用插件"},
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
        default=CONFIG_VERSION,
        description="配置兼容版本（与插件版本独立，请勿手动修改）",
        json_schema_extra={"label": "配置兼容版本", "disabled": True},
    )


class UserSlotConfig(PluginConfigBase):
    """单个用户条目，每项拥有独立的 QQ、关系称呼、群范围和便签。"""

    enabled: bool = Field(
        default=True,
        description="是否启用此用户（关闭只是临时停用、不清空该用户数据；QQ 为空时即使启用也会被跳过）",
        json_schema_extra={"label": "启用此用户"},
    )
    qq: str = Field(
        default="",
        description="该用户的 QQ 号，留空表示此槽位未配置",
        json_schema_extra={"label": "用户 QQ 号", "placeholder": "留空不认证"},
    )
    nickname: str = Field(
        default="用户",
        description="对该身份的关系称呼，会替换模板中的 {relation}",
        json_schema_extra={"label": "关系称呼", "placeholder": "例如：主人、老师"},
    )
    group_id: str = Field(
        default="",
        description=(
            "限制该用户身份只在某个群生效：填群号则该用户仅在这个群被认作用户，"
            "留空则所有聊天流（含私聊）都生效。同一 QQ 可在不同槽位分别配全局与群专属，群专属优先。"
        ),
        json_schema_extra={"label": "限制群聊（群号）", "placeholder": "留空全局生效；同一 QQ 群专属优先"},
    )
    planner_prompt_template: str = Field(
        default=DEFAULT_PLANNER_TEMPLATE,
        description=(
            "该用户专属的 Planner 规划便签（支持多行，留空则 Planner 只接收可信身份标识）。占位符："
            "{relation}=关系称呼，{display_name}=聊天显示名，{user_id}=已认证账号，"
            "{message}=该用户消息，{relations}=所有启用身份的关系称呼"
        ),
        json_schema_extra={"label": "Planner 规划便签", "placeholder": "留空仅传递身份；支持 {relation}、{message} 等占位符"},
    )
    replyer_prompt_template: str = Field(
        default=DEFAULT_REPLYER_TEMPLATE,
        description=(
            "该用户专属的 Replyer 回复便签（支持多行，留空则 Replyer 只接收可信身份凭证）。占位符："
            "{relation}=关系称呼，{display_name}=聊天显示名，{user_id}=已认证账号，"
            "{message}=发言者本次消息，{relations}=所有启用身份的关系称呼"
        ),
        json_schema_extra={"label": "Replyer 回复便签", "placeholder": "留空仅传递身份；支持 {relation}、{message} 等占位符"},
    )


class UsersSectionConfig(PluginConfigBase):
    """由原生列表控件管理用户，不再额外配置用户数量。"""

    __ui_label__ = "用户"
    __ui_icon__ = "users"
    __ui_order__ = 2

    entries: List[UserSlotConfig] = Field(
        default_factory=list,
        description="用户身份列表，添加、删除或排序后保存即可生效，无需重载插件。",
        json_schema_extra={
            "label": "用户身份列表",
            "hint": (
                "点击「添加项目」新增用户；删除或拖动排序后需保存。关闭「启用此用户」仅停用，不删除数据。"
                "同一 QQ 群专属优先；相同范围重复时取列表中第一个。"
                "两张便签留空时仅传递身份，支持 {relation}、{display_name}、{user_id}、{message}、{relations}。"
                "原生列表以单行框编辑便签；已有多行内容会保留，多行编辑请使用配置源码模式。"
            ),
        },
    )


class OwnerAuthConfig(PluginConfigBase):
    """固定配置结构；用户增减仅改变列表数据。"""

    __ui_label__ = "用户身份验证"

    # 宿主要求 [plugin].config_version 存在。
    plugin: GeneralSectionConfig = Field(default_factory=GeneralSectionConfig)
    users: UsersSectionConfig = Field(default_factory=UsersSectionConfig)
