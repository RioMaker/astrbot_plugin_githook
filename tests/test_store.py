import pytest
from astrbot_plugin_githook.catalog import PluginInfo
from astrbot_plugin_githook.store import Store


def info(name):
    return PluginInfo(name, name, "1.0", "RioMaker/" + name, "已启用")


def test_silent_baseline_new_install_reload_and_restart(tmp_path):
    path = tmp_path / "state.db"
    first = Store(path)
    first.set_enabled("bot-a:GroupMessage:1", True, "admin")
    assert first.sync_plugins([info("a")], "rio") == []
    assert first.pending() == []
    assert first.sync_plugins([info("a"), info("b")], "rio") == [info("b")]
    assert len(first.pending()) == 1
    first.close()
    second = Store(path)
    try:
        assert second.enabled("bot-a:GroupMessage:1")
        assert second.sync_plugins([info("a"), info("b")], "rio") == []
        assert len(second.pending()) == 1
        second.sync_plugins([], "rio")
        assert second.sync_plugins([info("a"), info("b")], "rio") == []
    finally:
        second.close()


def test_owner_change_is_silent(store):
    store.sync_plugins([info("a")], "rio")
    store.set_enabled("bot:GroupMessage:1", True)
    assert store.sync_plugins([info("a"), info("b")], "rio,new") == []
    assert store.pending() == []


def test_delivery_per_group_retry_disable_and_persistence(store):
    one, two = "a:GroupMessage:1", "b:GroupMessage:1"
    store.set_enabled(one, True)
    store.set_enabled(two, True)
    assert store.record_push("delivery", "hash", "hello", [])
    assert not store.record_push("delivery", "hash", "hello", [])
    pending = store.pending()
    assert len(pending) == 2
    store.sent(pending[0])
    store.failed(pending[1])
    assert store.delivery_counts() == {"sent": 1, "pending": 1}
    store.db.execute("UPDATE outbox SET next_try=0")
    assert [i["umo"] for i in store.pending()] == [two]
    store.set_enabled(two, False)
    store.set_enabled(two, True)
    assert store.pending() == []
    assert store.enabled(one)


def test_no_replay_for_late_subscriber_and_conflicting_id(store):
    store.record_push("first", "hash", "past", [])
    store.set_enabled("b:GroupMessage:1", True)
    assert not store.record_push("first", "hash", "past", [])
    assert store.pending() == []
    with pytest.raises(ValueError, match="不同内容"):
        store.record_push("first", "changed", "bad", [])


def test_parts_keep_order_retry_exhaustion_and_commit_dedup(store):
    store.set_enabled("b:GroupMessage:1", True)
    commit = {"repo": "RioMaker/a", "sha": "abc", "title": "first", "time": "2026-01-01T00:00:00Z"}
    store.record_push("first", "hash", "x" * 3300, [commit, commit])
    assert store.db.execute("SELECT count(*) FROM commits").fetchone()[0] == 1
    assert len(store.pending()) == 1
    first = store.pending()[0]
    assert first["part"] == 0
    store.sent(first)
    second = store.pending()[0]
    assert second["part"] == 1
    for attempt in range(8):
        second["attempts"] = attempt
        store.failed(second)
    assert store.pending() == []
    assert store.delivery_counts() == {"failed": 2, "sent": 1}
