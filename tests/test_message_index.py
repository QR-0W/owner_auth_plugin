from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncio
import importlib.util
import sys
import time
import types


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "owner_auth_plugin_testpkg"


def _load_plugin_module():
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PLUGIN_ROOT)]
    sys.modules.setdefault(PACKAGE_NAME, package)
    module_name = f"{PACKAGE_NAME}.plugin"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_ROOT / "plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


plugin_module = _load_plugin_module()
OwnerAuthPlugin = plugin_module.OwnerAuthPlugin
SenderSnapshot = plugin_module.SenderSnapshot


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str, **kwargs) -> None:
        del kwargs
        self.messages.append(message)

    info = debug
    warning = debug
    error = debug


class FakeConfigApi:
    async def get(self, key: str, default=""):
        return "999999" if key == "bot.qq_account" else default


class FakeMessageApi:
    def __init__(self) -> None:
        self.responses: dict[str, dict] = {}
        self.queries: list[str] = []

    async def get_by_id(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(message_id)
        raw = self.responses.get(message_id)
        if isinstance(raw, dict) and "success" in raw:
            return raw
        return {"success": True, "message": raw} if isinstance(raw, dict) else {"success": True, "message": None}


class FakeContext:
    def __init__(self) -> None:
        self.config = FakeConfigApi()
        self.message = FakeMessageApi()
        self.logger = FakeLogger()


def _message(
    *,
    session_id: str,
    message_id: str,
    user_id: str,
    nickname: str,
    group_id: str = "1000000001",
) -> dict:
    return {
        "platform": "qq",
        "session_id": session_id,
        "message_id": message_id,
        "processed_plain_text": f"message from {nickname}",
        "message_info": {
            "user_info": {
                "user_id": user_id,
                "user_nickname": nickname,
                "user_cardname": "",
            },
            "group_info": {"group_id": group_id} if group_id else None,
        },
    }


def _plugin() -> tuple[OwnerAuthPlugin, FakeContext]:
    plugin = OwnerAuthPlugin()
    context = FakeContext()
    plugin._ctx = context
    asyncio.run(plugin.on_load())
    return plugin, context


def test_exact_index_never_falls_back_to_recent_sender() -> None:
    plugin, context = _plugin()
    asyncio.run(
        plugin.index_sender_message(
            message=_message(session_id="group-a", message_id="msg-a", user_id="10001", nickname="A")
        )
    )
    asyncio.run(
        plugin.index_sender_message(
            message=_message(session_id="group-a", message_id="msg-b", user_id="10002", nickname="B")
        )
    )

    snapshot, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "msg-a"))
    assert snapshot is not None
    assert snapshot.speaker_qq == "10001"
    assert source == "message_index"
    assert diag == ""

    missing, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "missing"))
    assert missing is None
    assert source == "none"
    assert "message_index未命中" in diag
    assert context.message.queries == ["missing"]


def test_same_message_id_is_isolated_by_session() -> None:
    plugin, _ = _plugin()
    asyncio.run(
        plugin.index_sender_message(
            message=_message(session_id="group-a", message_id="same", user_id="10001", nickname="A")
        )
    )
    asyncio.run(
        plugin.index_sender_message(
            message=_message(session_id="group-b", message_id="same", user_id="10002", nickname="B")
        )
    )

    group_a, _, _ = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "same"))
    group_b, _, _ = asyncio.run(plugin._resolve_speaker_snapshot("group-b", "same"))
    assert group_a is not None and group_a.speaker_qq == "10001"
    assert group_b is not None and group_b.speaker_qq == "10002"


def test_database_lookup_requires_matching_session_and_message_id() -> None:
    plugin, context = _plugin()
    context.message.responses["db-hit"] = {
        "success": True,
        "message": _message(
            session_id="group-a",
            message_id="db-hit",
            user_id="10003",
            nickname="database user",
        ),
    }
    snapshot, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "db-hit"))
    assert snapshot is not None and snapshot.speaker_qq == "10003"
    assert source == "reply_id"
    assert diag == ""

    context.message.responses["wrong-session"] = {
        "success": True,
        "message": _message(
            session_id="group-b",
            message_id="wrong-session",
            user_id="10004",
            nickname="wrong group",
        ),
    }
    snapshot, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "wrong-session"))
    assert snapshot is None
    assert source == "none"
    assert "by_id会话不匹配" in diag


def test_message_index_is_bounded_per_session() -> None:
    plugin, _ = _plugin()
    for index in range(plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION + 1):
        plugin._cache_sender_snapshot(
            SenderSnapshot(
                platform="qq",
                session_id="group-a",
                message_id=f"msg-{index}",
                speaker_qq="10001",
                display_name="A",
                group_id="1000000001",
                is_private=False,
                processed_plain_text="test",
                cached_at=float(index + 1),
            )
        )

    assert plugin._message_index_size() == plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION
    assert plugin._get_cached_snapshot("group-a", "msg-0") is None
    assert plugin._get_cached_snapshot("group-a", f"msg-{plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION}") is not None


# ═══════════════════════════════════════════════════════════════════
# SessionIndex 互斥双淘汰层回归测试
# ═══════════════════════════════════════════════════════════════════


def test_identity_message_survives_ordinary_flood() -> None:
    """身份消息写入后，紧接着灌入超过普通上限的普通消息，该身份消息仍应命中。"""
    plugin, context = _plugin()
    # 建立最低配置：让 1900000001 进入保护候选
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq",
            session_id="group-a",
            message_id="owner-msg",
            speaker_qq="1900000001",
            display_name="TestUser1",
            group_id="1000000001",
            is_private=False,
            processed_plain_text="主人消息",
            cached_at=1.0,
        )
    )
    for index in range(plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION + 1):
        plugin._cache_sender_snapshot(
            SenderSnapshot(
                platform="qq",
                session_id="group-a",
                message_id=f"ord-{index}",
                speaker_qq="10001",
                display_name="A",
                group_id="1000000001",
                is_private=False,
                processed_plain_text="普通消息",
                cached_at=float(index + 2),
            )
        )

    snapshot = plugin._get_cached_snapshot("group-a", "owner-msg")
    assert snapshot is not None
    assert snapshot.speaker_qq == "1900000001"


def test_protected_layer_caps_at_limit() -> None:
    """保护层达到上限后淘汰最旧身份消息，新身份消息保留，快照总数不超过 256。"""
    plugin, context = _plugin()
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    limit = plugin_module._PROTECTED_INDEX_LIMIT_PER_SESSION
    for index in range(limit + 1):
        plugin._cache_sender_snapshot(
            SenderSnapshot(
                platform="qq",
                session_id="group-a",
                message_id=f"prot-{index}",
                speaker_qq="1900000001",
                display_name=f"User{index}",
                group_id="1000000001",
                is_private=False,
                processed_plain_text="test",
                cached_at=float(index + 1),
            )
        )

    assert plugin._get_cached_snapshot("group-a", "prot-0") is None
    assert plugin._get_cached_snapshot("group-a", f"prot-{limit}") is not None
    assert plugin._message_index_size() == limit


def test_same_message_id_coverage_protected_to_ordinary() -> None:
    """同号先写入身份用户消息再被普通用户覆盖，快照应按最新值为准。"""
    plugin, context = _plugin()
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="group-a", message_id="msg-x",
            speaker_qq="1900000001", display_name="Owner", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=1.0,
        )
    )
    assert plugin._get_cached_snapshot("group-a", "msg-x").speaker_qq == "1900000001"

    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="group-a", message_id="msg-x",
            speaker_qq="2000000002", display_name="Normie", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=2.0,
        )
    )
    assert plugin._get_cached_snapshot("group-a", "msg-x").speaker_qq == "2000000002"
    # 快照只存一份
    assert plugin._message_index_size() == 1


def test_same_message_id_coverage_ordinary_to_protected() -> None:
    """同号先写入普通用户，后写入身份用户，快照按最新且进入保护层。"""
    plugin, context = _plugin()
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="group-a", message_id="msg-y",
            speaker_qq="2000000002", display_name="Normie", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=1.0,
        )
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="group-a", message_id="msg-y",
            speaker_qq="1900000001", display_name="Owner", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=2.0,
        )
    )
    assert plugin._get_cached_snapshot("group-a", "msg-y").speaker_qq == "1900000001"
    assert plugin._message_index_size() == 1


def test_host_query_passes_chat_id() -> None:
    """Host get_by_id 应传递 chat_id；Mock 验证该参数已传出。"""
    plugin, context = _plugin()

    class TrackingApi:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def get_by_id(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append({"message_id": message_id, "kwargs": dict(kwargs)})
            return {"success": True, "message": None}

    context.message = TrackingApi()
    asyncio.run(plugin._resolve_speaker_snapshot("group-a", "test-msg"))
    assert len(context.message.calls) == 1
    assert context.message.calls[0]["kwargs"].get("chat_id") == "group-a"


def test_host_query_handles_success_false() -> None:
    """Host 返回 success=false 时应明确失败并跳过注入。"""
    plugin, context = _plugin()

    class ErrorApi:
        async def get_by_id(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
            return {"success": False, "error": "数据库超时"}

    context.message = ErrorApi()
    snapshot, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "test-msg"))
    assert snapshot is None
    assert source == "none"
    assert "数据库超时" in diag


def test_host_query_exception_is_graceful() -> None:
    """Host 调用抛异常时应优雅失败而非崩溃。"""
    plugin, context = _plugin()

    class CrashApi:
        async def get_by_id(self, message_id: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("连接中断")

    context.message = CrashApi()
    snapshot, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "test-msg"))
    assert snapshot is None
    assert source == "none"
    assert "by_id异常" in diag


def test_reclassify_moves_between_layers() -> None:
    """配置热更新后重分类：让 slot QQ 变更，原保护消息变为普通；原 QQ 删除后普通消息可能变为保护。"""
    plugin, context = _plugin()
    # 初始：1900000001 在保护，2000000002 在普通
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="group-a", message_id="msg-1",
            speaker_qq="1900000001", display_name="A1", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=1.0,
        )
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="group-a", message_id="msg-2",
            speaker_qq="2000000002", display_name="A2", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=2.0,
        )
    )
    # 热更新：user1 的 QQ 改成 2000000002
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="2000000002", nickname="主人", group_id=""),
    )
    plugin._reclassify_session_snapshots()

    assert plugin._get_cached_snapshot("group-a", "msg-1") is not None
    assert plugin._get_cached_snapshot("group-a", "msg-2") is not None
    assert plugin._message_index_size() == 2


def test_ttl_purges_both_layers_together() -> None:
    """TTL 过期后整个 SessionIndex 被移除，普通层和保护层同时消失。"""
    plugin, context = _plugin()
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True,
                               cache_ttl_seconds=1),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    old_time = time.time() - 9999
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="stale-session", message_id="prot",
            speaker_qq="1900000001", display_name="P", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=old_time,
        )
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="stale-session", message_id="ord",
            speaker_qq="2000000002", display_name="O", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=old_time,
        )
    )
    plugin._purge_message_index()
    assert plugin._get_cached_snapshot("stale-session", "prot") is None
    assert plugin._get_cached_snapshot("stale-session", "ord") is None
    assert plugin._message_index_size() == 0


def test_active_session_not_purged() -> None:
    """活跃会话的索引不被 TTL 清理，仍可精确命中。"""
    plugin, context = _plugin()
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True,
                               cache_ttl_seconds=1),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    plugin._cache_sender_snapshot(
        SenderSnapshot(
            platform="qq", session_id="active", message_id="msg",
            speaker_qq="1900000001", display_name="A", group_id="1000000001",
            is_private=False, processed_plain_text="", cached_at=time.time(),
        )
    )
    plugin._purge_message_index()
    assert plugin._get_cached_snapshot("active", "msg") is not None


def test_max_snapshots_per_session_is_256() -> None:
    """保护层和普通层各满 128 时，单一会话快照总数不超过 256。"""
    plugin, context = _plugin()
    plugin._plugin_config_instance = SimpleNamespace(
        plugin=SimpleNamespace(enabled=True, user_count=1, enable_private_inject=True),
        user1=SimpleNamespace(enabled=True, qq="1900000001", nickname="主人", group_id=""),
    )
    p_lim = plugin_module._PROTECTED_INDEX_LIMIT_PER_SESSION
    o_lim = plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION
    for i in range(p_lim):
        plugin._cache_sender_snapshot(
            SenderSnapshot(
                platform="qq", session_id="s", message_id=f"p{i}",
                speaker_qq="1900000001", display_name="P", group_id="1000000001",
                is_private=False, processed_plain_text="", cached_at=float(i + 1),
            )
        )
    for i in range(o_lim):
        plugin._cache_sender_snapshot(
            SenderSnapshot(
                platform="qq", session_id="s", message_id=f"o{i}",
                speaker_qq="2000000002", display_name="O", group_id="1000000001",
                is_private=False, processed_plain_text="", cached_at=float(p_lim + i + 1),
            )
        )
    assert plugin._message_index_size() == p_lim + o_lim
