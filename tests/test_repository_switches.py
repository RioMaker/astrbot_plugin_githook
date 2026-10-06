import asyncio
import hashlib
import hmac
import json
import sqlite3

import aiohttp
import pytest
from aiohttp import web
from astrbot_plugin_githook import main
from astrbot_plugin_githook.catalog import repository_info, resolve
from astrbot_plugin_githook.config import Settings
from astrbot_plugin_githook.github import GitHubClient
from astrbot_plugin_githook.registry import RepositoryRegistry
from astrbot_plugin_githook.store import Store
from astrbot_plugin_githook.webhook import Webhook
from test_http import server
from test_main import Context


class SavedConfig(dict):
    def __init__(self, path, values):
        super().__init__(values)
        self.path = path
        self.saves = 0

    def save_config(self):
        self.path.write_text(json.dumps(self), encoding="utf-8")
        self.saves += 1


def test_schema_has_checkbox_defaults_and_registry_preserves_manual_choices(tmp_path):
    assert Settings().watch_source == "owner"
    assert not Settings().repo_enabled("RioMaker/astrbot_plugin_liuyao")
    schema = json.loads((main.Path(main.__file__).parent / "_conf_schema.json").read_text("utf-8"))
    control = schema["repository_switches"]
    assert control["default"] == []
    assert control["templates"]["repository"]["items"]["enabled"]["default"] is False
    cfg = SavedConfig(
        tmp_path / "config.json",
        {"repository_switches": [{"repo": "RioMaker/astrbot_plugin_first", "enabled": True}]},
    )
    registry = RepositoryRegistry(cfg)
    first = repository_info("RioMaker/astrbot_plugin_first")
    new = repository_info("RioMaker/astrbot_plugin_new")
    assert registry.sync([first, new]) == [new.repo]
    assert registry.enabled(first.repo) and not registry.enabled(new.repo)
    persisted = json.loads(cfg.path.read_text("utf-8"))
    assert persisted["repository_switches"][1]["enabled"] is False
    registry.sync([first, new])
    assert cfg.saves == 1
    # A later scan must preserve a manual change and default-close the next repo.
    cfg["repository_switches"][0]["enabled"] = False
    cfg["repository_switches"][1]["enabled"] = True
    newer = repository_info("RioMaker/astrbot_plugin_newer")
    registry.sync([newer, first, new])
    assert not registry.enabled(first.repo)
    assert registry.enabled(new.repo)
    assert not registry.enabled(newer.repo)
    restarted = RepositoryRegistry(json.loads(cfg.path.read_text("utf-8")))
    assert restarted.enabled(new.repo) and not restarted.enabled(newer.repo)


@pytest.mark.parametrize(
    "rows",
    [
        [{"repo": "RioMaker/astrbot_plugin_x", "enabled": "true"}],
        [{"repo": "https://evil/abc", "enabled": True}],
        [{"repo": "", "enabled": True}],
        [
            {"repo": "RioMaker/astrbot_plugin_x", "enabled": True},
            {"repo": "riomaker/astrbot_plugin_x", "enabled": False},
        ],
    ],
)
def test_invalid_switches_never_become_implicit_enables(rows):
    with pytest.raises(ValueError):
        Settings.load({"repository_switches": rows})


def test_failed_config_save_restores_manual_choices(tmp_path):
    cfg = SavedConfig(
        tmp_path / "config.json",
        {"repository_switches": [{"repo": "RioMaker/astrbot_plugin_old", "enabled": True}]},
    )

    def fail():
        raise OSError("test disk failure")

    cfg.save_config = fail
    registry = RepositoryRegistry(cfg)
    with pytest.raises(OSError):
        registry.sync([repository_info("RioMaker/astrbot_plugin_new")])
    assert registry.enabled("RioMaker/astrbot_plugin_old")
    assert not registry.enabled("RioMaker/astrbot_plugin_new")
    assert len(cfg["repository_switches"]) == 1


def test_owner_api_pagination_organizations_and_private_filter(store):
    calls = []

    async def identity(request):
        return web.json_response(
            {"type": "Organization" if request.match_info["owner"] == "Team" else "User"}
        )

    async def repositories(request):
        calls.append(request.path)
        if request.path == "/user/repos":
            assert request.query["affiliation"] == "owner,organization_member"
            assert request.headers["Authorization"] == "Bearer test-read-token"
            return web.json_response(
                [
                    {"full_name": "RioMaker/astrbot_plugin_private"},
                    {"full_name": "Other/astrbot_plugin_private"},
                    {"full_name": "RioMaker/Astrbot_plguin_dev"},
                ]
            )
        if request.match_info["owner"] == "Team":
            return web.json_response([{"full_name": "Team/astrbot_plugin_tool"}])
        entries = [{"full_name": f"RioMaker/ordinary_{i}"} for i in range(105)]
        entries[101] = {"full_name": "RioMaker/astrbot_plugin_outside_workspace"}
        entries[102] = {"full_name": "RioMaker/astrbot_plugin_legacy-name"}
        entries[103] = {"full_name": "RioMaker/astrbot_plugin_"}
        page = int(request.query["page"])
        return web.json_response(entries[(page - 1) * 100 : page * 100])

    async def run():
        app = web.Application()
        app.router.add_get("/users/{owner}", identity)
        app.router.add_get("/users/{owner}/repos", repositories)
        app.router.add_get("/orgs/{owner}/repos", repositories)
        app.router.add_get("/user/repos", repositories)
        async with server(app) as base, aiohttp.ClientSession() as session:
            client = GitHubClient(session, store, "test-read-token", api_base=base)
            settings = Settings(owners=("RioMaker", "Team"))
            plugins, warnings, stale = await client.owner_catalog(settings)
            assert {p.repo for p in plugins} == {
                "RioMaker/astrbot_plugin_outside_workspace",
                "RioMaker/astrbot_plugin_legacy-name",
                "RioMaker/astrbot_plugin_private",
                "Team/astrbot_plugin_tool",
            }
            assert not warnings and not stale
            assert calls.count("/users/RioMaker/repos") == 2
            assert "/orgs/Team/repos" in calls
            assert resolve(plugins, "private").repo == "RioMaker/astrbot_plugin_private"
            count = len(calls)
            await client.owner_catalog(settings)
            assert len(calls) == count

    asyncio.run(run())


def test_owner_webhook_discovery_default_off_manual_on_cancel_and_restart(monkeypatch, tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(main, "get_astrbot_data_path", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(main, "get_astrbot_plugin_path", lambda: str(root))
    cfg = SavedConfig(tmp_path / "config.json", {"webhook_secret": "test-only-secret"})
    context = Context()
    state = {"repos": ["astrbot_plugin_one", "astrbot_plugin_two"], "offline": False}

    async def identity(request):
        if state["offline"]:
            return web.Response(status=503)
        return web.json_response({"type": "User"})

    async def repositories(request):
        if state["offline"]:
            return web.Response(status=503)
        return web.json_response([{"full_name": "RioMaker/" + name} for name in state["repos"]])

    async def run():
        app = web.Application()
        app.router.add_get("/users/RioMaker", identity)
        app.router.add_get("/users/RioMaker/repos", repositories)
        p = main.GithookPlugin(context, cfg)
        handler = Webhook(
            p.settings,
            p.store,
            lambda: p.plugins,
            p._refresh_for_webhook,
            p.wake,
            is_repo_enabled=p.repo_enabled,
            register_repo=p._register_repository,
        )
        app.router.add_post("/hook", handler.handle)
        async with server(app) as base, aiohttp.ClientSession() as session:
            p.github = GitHubClient(session, p.store, api_base=base)
            assert not p.store.enabled("one:GroupMessage:1")
            p.store.set_enabled("one:GroupMessage:1", True)
            try:
                await p.refresh()
                assert len(p.plugins) == 2
                assert all(not row["enabled"] for row in cfg["repository_switches"])

                async def post(repo, delivery, bad_signature=False, invalid=False):
                    payload = {
                        "repository": {"full_name": repo},
                        "ref": "refs/heads/main",
                        "commits": [{"id": "bad"}] if invalid else [],
                    }
                    body = json.dumps(payload).encode()
                    signature = hmac.new(b"test-only-secret", body, hashlib.sha256).hexdigest()
                    async with session.post(
                        base + "/hook",
                        data=body,
                        headers={
                            "X-GitHub-Event": "push",
                            "X-GitHub-Delivery": delivery,
                            "X-Hub-Signature-256": "bad"
                            if bad_signature
                            else "sha256=" + signature,
                        },
                    ) as response:
                        text = await response.text()
                        return response.status, json.loads(text) if response.status < 300 else text

                repo = "RioMaker/astrbot_plugin_one"
                assert (await post(repo, "disabled"))[1]["reason"] == "repository disabled"
                assert p.store.pending() == []
                assert context.sent == []
                for row in cfg["repository_switches"]:
                    if row["repo"] == repo:
                        row["enabled"] = True
                cfg.save_config()  # Same object update used by AstrBot's config save route.
                assert (await post(repo, "disabled"))[1]["reason"] == "duplicate delivery"
                assert (await post(repo, "enabled-1"))[1]["status"] == "queued"
                assert len(p.store.pending()) == 1

                # All newly discovered repos remain off, despite another enabled repo.
                future = "RioMaker/astrbot_plugin_future"
                async with p.scan_lock:
                    response = await asyncio.wait_for(post(future, "future"), 1)
                assert response[1]["reason"] == "repository disabled"
                assert not p.repo_enabled(future)
                assert any(
                    row["repo"] == future and not row["enabled"]
                    for row in cfg["repository_switches"]
                )
                assert len(p.store.pending()) == 1
                assert (await post("Other/astrbot_plugin_x", "other"))[1]["status"] == "ignored"
                assert (await post("RioMaker/ordinary_x", "ordinary"))[1]["status"] == "ignored"
                assert (await post("RioMaker/astrbot_plugin_invalid", "invalid", invalid=True))[
                    0
                ] == 400
                assert (
                    await post("RioMaker/astrbot_plugin_unsigned", "unsigned", bad_signature=True)
                )[0] == 401
                assert len(cfg["repository_switches"]) == 3

                # An API scan adds another off row and preserves the manual on row.
                state["repos"].append("astrbot_plugin_scanned_new")
                p.store.db.execute("UPDATE api_cache SET saved=0 WHERE key LIKE '/users/%'")
                await p.refresh()
                assert p.repo_enabled(repo)
                assert not p.repo_enabled("RioMaker/astrbot_plugin_scanned_new")
                assert len(p.store.pending()) == 1
                assert len(json.loads(cfg.path.read_text("utf-8"))["repository_switches"]) == 4

                # Closing cancels a previously accepted delivery; re-opening cannot replay it.
                next(row for row in cfg["repository_switches"] if row["repo"] == repo)[
                    "enabled"
                ] = False
                cfg.save_config()
                await p.refresh()
                assert p.store.pending() == []
                assert p.store.delivery_counts()["cancelled"] == 1
                next(row for row in cfg["repository_switches"] if row["repo"] == repo)[
                    "enabled"
                ] = True
                cfg.save_config()
                assert (await post(repo, "enabled-1"))[1]["reason"] == "duplicate delivery"
                assert (await post(repo, "enabled-2"))[1]["status"] == "queued"
                sender = asyncio.create_task(p._sender_loop())
                p.tasks.append(sender)
                for _ in range(100):
                    if context.sent:
                        break
                    await asyncio.sleep(0.01)
                assert len(context.sent) == 1
                assert repo in context.sent[0][1]
                assert p.store.pending() == []
            finally:
                await p.terminate()

        saved = SavedConfig(cfg.path, json.loads(cfg.path.read_text("utf-8")))
        again = main.GithookPlugin(context, saved)
        try:
            await again.refresh()  # No API client: keep discovered repos and checkbox choices.
            assert len(again.plugins) == 4
            assert again.repo_enabled("RioMaker/astrbot_plugin_one")
            assert not again.repo_enabled("RioMaker/astrbot_plugin_future")
            assert again.store.pending() == []
        finally:
            await again.terminate()

    asyncio.run(run())


def test_legacy_delivery_migration_cannot_bypass_repository_switches(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE deliveries(id TEXT PRIMARY KEY, digest TEXT NOT NULL, created REAL NOT NULL);
        INSERT INTO deliveries VALUES ('legacy','hash',0);
    """)
    db.close()
    store = Store(path)
    try:
        store.set_enabled("one:GroupMessage:1", True)
        store.db.execute(
            "INSERT INTO outbox(delivery,umo,part,message) VALUES ('legacy','one:GroupMessage:1',0,'old')"
        )
        store.record_push("new", "hash", "new", [], repo="RioMaker/astrbot_plugin_one")
        store.cancel_disabled_repos({"riomaker/astrbot_plugin_one"})
        assert [item["message"] for item in store.pending()] == ["new"]
        store.cancel_disabled_repos(set())
        assert store.pending() == []
    finally:
        store.close()
