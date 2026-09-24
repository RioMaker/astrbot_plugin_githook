import asyncio
import inspect
import json
import socket
from types import SimpleNamespace

from astrbot_plugin_githook import main
from astrbot_plugin_githook.github import HistoryResult


class Event:
    def __init__(self, admin=False, group="1", platform="one", role="member"):
        self.admin, self.group = admin, group
        self.unified_msg_origin = (
            f"{platform}:GroupMessage:{group}" if group else f"{platform}:FriendMessage:user"
        )
        self.message_obj = SimpleNamespace(sender={"role": role})
        self.stopped = False

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return "admin" if self.admin else "normal"

    def is_admin(self):
        return self.admin

    def stop_event(self):
        self.stopped = True

    def plain_result(self, value):
        return value


class Context:
    def __init__(self):
        self.sent = []
        self.fail = set()

    def get_all_stars(self):
        return [SimpleNamespace(name="astrbot_plugin_liuyao", activated=True, star_cls=object())]

    async def send_message(self, umo, chain):
        if umo in self.fail:
            raise RuntimeError("test failure")
        assert isinstance(chain, main.MessageChain)
        self.sent.append((umo, chain.chain[0].text))
        return True


def setup(monkeypatch, tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    folder = plugins / "astrbot_plugin_liuyao"
    folder.mkdir()
    (folder / "metadata.yaml").write_text(
        json.dumps(
            {
                "name": "astrbot_plugin_liuyao",
                "repo": "https://github.com/RioMaker/astrbot_plugin_liuyao",
                "version": "0.9.0",
                "display_name": "六爻起卦",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_astrbot_data_path", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(main, "get_astrbot_plugin_path", lambda: str(plugins))
    return plugins


async def ask(plugin, event, command):
    return "\n".join([part async for part in plugin.command(event, command)])


def test_greedystr_annotation_has_no_default():
    param = inspect.signature(main.GithookPlugin.command).parameters["content"]
    # AstrBot CommandFilter treats a default value as the argument type and would
    # discard all but the first word if this were content: GreedyStr = "".
    assert param.annotation is main.GreedyStr
    assert param.default is inspect.Parameter.empty


def test_astrbot_admin_only_group_switches_and_queries(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)

    async def run():
        p = main.GithookPlugin(Context(), {})
        await p.initialize()
        try:
            for role in ("member", "admin", "owner"):
                assert "仅 AstrBot 管理员" in await ask(p, Event(role=role), "开")
                assert not p.store.enabled("one:GroupMessage:1")
            assert "群内" in await ask(p, Event(admin=True, group=""), "开")
            assert "已开启" in await ask(p, Event(admin=True), "开")
            assert p.store.enabled("one:GroupMessage:1")
            assert not p.store.enabled("two:GroupMessage:1")
            assert "0.9.0" in await ask(p, Event(), "六爻")
            assert "已启用" in await ask(p, Event(), "六爻")
            assert "六爻起卦" in await ask(p, Event(), "列表")
            assert "/githook 历史 六爻 页 2" in await ask(p, Event(), "帮助")
            assert "仅 AstrBot 管理员" in await ask(p, Event(), "关")
            assert p.store.enabled("one:GroupMessage:1")
            assert "已关闭" in await ask(p, Event(admin=True), "关")
            assert not p.store.enabled("one:GroupMessage:1")
        finally:
            await p.terminate()
        assert p.session.closed
        assert all(t.done() for t in p.tasks)

    asyncio.run(run())


def test_permission_and_origin_frozen_before_await(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)

    async def run():
        p = main.GithookPlugin(Context(), {})
        event = Event(admin=True)
        refresh = p.refresh

        async def mutate():
            event.admin = False
            event.unified_msg_origin = "other:GroupMessage:9"
            await refresh()

        monkeypatch.setattr(p, "refresh", mutate)
        try:
            await ask(p, event, "开")
            assert p.store.enabled("one:GroupMessage:1")
            assert not p.store.enabled("other:GroupMessage:9")
        finally:
            await p.terminate()

    asyncio.run(run())


def test_sender_independent_groups_and_shutdown_frees_port(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)

    async def run():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        context = Context()
        context.fail.add("two:GroupMessage:1")
        p = main.GithookPlugin(context, {"webhook_secret": "only-for-tests", "webhook_port": port})
        await p.initialize()
        try:
            p.store.set_enabled("one:GroupMessage:1", True)
            p.store.set_enabled("two:GroupMessage:1", True)
            p.store.record_push("test", "hash", "notification", [])
            p.wake.set()
            for _ in range(50):
                if p.store.delivery_counts().get("sent") == 1:
                    break
                await asyncio.sleep(0.01)
            assert context.sent == [("one:GroupMessage:1", "notification")]
            assert p.store.delivery_counts() == {"pending": 1, "sent": 1}
        finally:
            await p.terminate()
        assert all(t.done() for t in p.tasks)
        with socket.socket() as check:
            assert check.connect_ex(("127.0.0.1", port)) != 0

    asyncio.run(run())


def test_history_command_forwards_plugin_and_full_range(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)

    async def run():
        p = main.GithookPlugin(Context(), {})
        calls = []

        async def history(plugins, query, branch):
            calls.append((plugins, query, branch))
            return HistoryResult(
                [
                    {
                        "repo": "RioMaker/astrbot_plugin_liuyao",
                        "sha": "a" * 40,
                        "title": "feat: 测试标题",
                        "time": "2026-09-24T00:00:00Z",
                    }
                ],
                [],
            )

        p.github = SimpleNamespace(history=history)
        try:
            answer = await ask(p, Event(), "历史 六爻 2026-09-01..2026-09-24 页 2")
            assert "31. [astrbot_plugin_liuyao] feat: 测试标题" in answer
            assert "2026-09-24 08:00:00" in answer
            assert calls[0][0][0].name == "astrbot_plugin_liuyao"
            assert calls[0][1].start == 31
            assert calls[0][1].until == "2026-09-24T15:59:59Z"
            await ask(p, Event(), "历史")
            assert calls[-1][1].end == 30
        finally:
            await p.terminate()

    asyncio.run(run())


def test_load_hook_only_announces_new_owned_plugin(monkeypatch, tmp_path):
    root = setup(monkeypatch, tmp_path)

    async def run():
        p = main.GithookPlugin(Context(), {})
        try:
            await p.refresh()
            p.store.set_enabled("one:GroupMessage:1", True)
            for name, owner in (("mine", "RioMaker"), ("third", "Other")):
                folder = root / name
                folder.mkdir()
                (folder / "metadata.yaml").write_text(
                    json.dumps(
                        {
                            "name": name,
                            "repo": f"https://github.com/{owner}/{name}",
                            "version": "1.0",
                        }
                    ),
                    encoding="utf-8",
                )
            await p.on_plugin_loaded(None)
            assert p.scan_wake.is_set()
            await p.refresh()
            assert len(p.store.pending()) == 1
            assert "mine" in p.store.pending()[0]["message"]
            assert "third" not in p.store.pending()[0]["message"]
            await p.refresh()
            assert len(p.store.pending()) == 1
        finally:
            await p.terminate()

    asyncio.run(run())


def test_port_conflict_cleanup(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)

    async def run():
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            p = main.GithookPlugin(
                Context(), {"webhook_secret": "test", "webhook_port": occupied.getsockname()[1]}
            )
            try:
                await p.initialize()
            except OSError:
                assert p.closed
                assert p.session.closed
                assert p.runner is None
            else:
                await p.terminate()
                raise AssertionError("occupied port was accepted")

    asyncio.run(run())
