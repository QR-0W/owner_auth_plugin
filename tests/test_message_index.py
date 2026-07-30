from pathlib import Path

import asyncio
import importlib.util
import sys
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

    async def get_by_id(self, message_id: str):
        self.queries.append(message_id)
        return self.responses.get(message_id, {"message": None})


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
    group_id: str = "717566174",
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
        "message": _message(
            session_id="group-a",
            message_id="db-hit",
            user_id="10003",
            nickname="database user",
        )
    }
    snapshot, source, diag = asyncio.run(plugin._resolve_speaker_snapshot("group-a", "db-hit"))
    assert snapshot is not None and snapshot.speaker_qq == "10003"
    assert source == "reply_id"
    assert diag == ""

    context.message.responses["wrong-session"] = {
        "message": _message(
            session_id="group-b",
            message_id="wrong-session",
            user_id="10004",
            nickname="wrong group",
        )
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
                group_id="717566174",
                is_private=False,
                processed_plain_text="test",
                cached_at=float(index + 1),
            )
        )

    assert plugin._message_index_size() == plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION
    assert plugin._get_cached_snapshot("group-a", "msg-0") is None
    assert plugin._get_cached_snapshot("group-a", f"msg-{plugin_module._MESSAGE_INDEX_LIMIT_PER_SESSION}") is not None
