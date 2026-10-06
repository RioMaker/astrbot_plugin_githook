import asyncio
import base64
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from astrbot_plugin_githook import main
from astrbot_plugin_githook.catalog import workspace_candidates, workspace_info
from astrbot_plugin_githook.config import Settings
from astrbot_plugin_githook.github import GitHubClient
from astrbot_plugin_githook.webhook import Webhook


def entry(name, url=None):
    return {
        "directory": name,
        "url": url or f"https://github.com/RioMaker/{name}.git",
        "version": "1.0.0",
        "commit": "a" * 40,
        "branch": "main",
    }


def test_workspace_requires_exact_membership_and_three_matching_names():
    manifest = {
        "schema_version": 1,
        "plugins": [
            entry("astrbot_plugin_good"),
            entry("astrbot_plugin_legacy"),
            entry("astrbot_plugin_rp-master"),
            entry("astrbot_plugin_bad_repo", "RioMaker/astrbot_plugin_different"),
            entry("astrbot_plugin_other", "Other/astrbot_plugin_other"),
        ],
    }
    candidates = workspace_candidates(manifest, ("RioMaker",))
    assert [e["directory"] for e, _ in candidates] == [
        "astrbot_plugin_good",
        "astrbot_plugin_legacy",
    ]
    item, repo = candidates[0]
    info = workspace_info(item, repo, {"name": item["directory"], "repo": ""})
    assert info.repo == "RioMaker/astrbot_plugin_good"
    assert not info.installed
    assert workspace_info(item, repo, {"name": "good"}) is None
    assert workspace_info(item, repo, []) is None
    manifest["plugins"].append(entry("astrbot_plugin_good"))
    with pytest.raises(ValueError, match="重复"):
        workspace_candidates(manifest, ("RioMaker",))


@pytest.mark.parametrize(
    "config",
    [
        {"watch_source": "all_owners"},
        {"watch_source": "workspace", "workspace_repo": "https://evil/repo"},
        {"watch_source": "workspace", "workspace_manifest_path": "../plugins.json"},
        {"watch_source": "workspace", "workspace_manifest_path": "/plugins.json"},
        {"watch_source": "workspace", "workspace_manifest_path": "plugins.json?ref=x"},
    ],
)
def test_invalid_workspace_settings(config):
    with pytest.raises(ValueError):
        Settings.load(config)


@asynccontextmanager
async def serve(app):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)


def test_workspace_http_new_repo_filter_removal_cache_and_restart(monkeypatch, tmp_path):
    root = tmp_path / "plugins"
    folder = root / "astrbot_plugin_liuyao"
    folder.mkdir(parents=True)
    # ZIP metadata can have a missing/wrong repo; canonical workspace membership wins.
    (folder / "metadata.yaml").write_text(
        json.dumps({"name": folder.name, "version": "0.9.0", "repo": "Other/wrong"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_astrbot_data_path", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(main, "get_astrbot_plugin_path", lambda: str(root))
    config = {
        "watch_source": "workspace",
        "workspace_repo": "RioMaker/workspace",
        "github_token": "test-private-read-token",
        "webhook_secret": "test-signature-secret",
    }
    context = SimpleNamespace(get_all_stars=lambda: [])
    initial = [entry(folder.name), entry("astrbot_plugin_bad")]
    state = {"entries": initial, "error": False}
    requests = []

    async def contents(request):
        assert request.headers["Authorization"] == "Bearer test-private-read-token"
        requests.append(request.path)
        if state["error"]:
            return web.Response(status=401)
        repo, filename = request.match_info["repo"], request.match_info["filename"]
        if repo == "workspace":
            assert filename == "plugins.json"
            data = {"schema_version": 1, "plugins": state["entries"]}
        else:
            assert filename == "metadata.yaml"
            data = {
                "name": "bad" if repo == "astrbot_plugin_bad" else repo,
                "version": "1.0.0",
                "display_name": repo,
            }
        return web.json_response(
            {"encoding": "base64", "content": base64.b64encode(json.dumps(data).encode()).decode()}
        )

    async def run():
        app = web.Application()
        app.router.add_get("/repos/RioMaker/{repo}/contents/{filename}", contents)
        p = main.GithookPlugin(context, config)
        handler = Webhook(p.settings, p.store, lambda: p.plugins, p._refresh_for_webhook, p.wake)
        app.router.add_post("/hook", handler.handle)
        async with serve(app) as base, aiohttp.ClientSession() as session:
            p.github = GitHubClient(session, p.store, p.settings.github_token, api_base=base)
            p.store.set_enabled("one:GroupMessage:1", True)
            try:
                await p.refresh()
                assert [x.name for x in p.plugins] == [folder.name]
                assert p.plugins[0].installed and p.plugins[0].version == "0.9.0"
                assert p.store.pending() == []

                async def post(repo, delivery):
                    payload = {
                        "repository": {"full_name": "RioMaker/" + repo},
                        "ref": "refs/heads/main",
                        "commits": [],
                    }
                    body = json.dumps(payload).encode()
                    signature = hmac.new(
                        p.settings.webhook_secret.encode(), body, hashlib.sha256
                    ).hexdigest()
                    async with session.post(
                        base + "/hook",
                        data=body,
                        headers={
                            "X-GitHub-Event": "push",
                            "X-GitHub-Delivery": delivery,
                            "X-Hub-Signature-256": "sha256=" + signature,
                        },
                    ) as response:
                        return response.status, await response.json()

                count = len(requests)
                # An owner's same-prefix repo outside the exact workspace remains excluded.
                async with p.scan_lock:
                    assert (await asyncio.wait_for(post("astrbot_plugin_outside", "outside"), 1))[
                        1
                    ]["status"] == "ignored"
                assert (await post("astrbot_plugin_bad", "bad"))[1]["status"] == "ignored"
                assert len(requests) == count  # Webhook acceptance never waits on remote API.

                state["entries"] = initial + [entry("astrbot_plugin_future")]
                p.store.db.execute("UPDATE api_cache SET saved=? WHERE key LIKE '/repos/%'", (0,))
                await p.refresh()
                future = next(x for x in p.plugins if x.name == "astrbot_plugin_future")
                assert not future.installed
                assert len(p.store.pending()) == 1
                assert "新增插件" in p.store.pending()[0]["message"]
                assert (await post(future.name, "future-push"))[1]["status"] == "queued"
                assert len(p.store.pending()) == 2
                assert p.store.cache_get("webhook:last")[0]["repo"] == future.repo
                await p.refresh()
                assert len(p.store.pending()) == 2

                # A failure uses old verified data without making it look freshly verified.
                key = "workspace-catalog:" + p._scope_key()
                saved = p.store.cache_get(key)[1]
                p.store.db.execute(
                    "UPDATE api_cache SET saved=? WHERE key LIKE '/repos/%'", (time.time() - 600,)
                )
                state["error"] = True
                await p.refresh()
                assert "旧缓存" in p.catalog_status
                assert p.store.cache_get(key)[1] == saved
                assert {x.name for x in p.plugins} == {folder.name, future.name}

                # Restore connectivity and remove a member: its next push must be ignored.
                state["error"] = False
                state["entries"] = [entry(future.name)]
                await p.refresh()
                assert [x.name for x in p.plugins] == [future.name]
                assert (await post(folder.name, "removed"))[1]["status"] == "ignored"
            finally:
                await p.terminate()

        again = main.GithookPlugin(context, config)
        try:
            await again.refresh()  # No GitHub client: preserve the verified exact snapshot.
            assert [x.name for x in again.plugins] == ["astrbot_plugin_future"]
            assert "旧清单" in again.catalog_status
            assert len(again.store.pending()) == 2
        finally:
            await again.terminate()

    asyncio.run(run())


def test_workspace_offline_snapshot_is_source_bound(monkeypatch, tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(main, "get_astrbot_data_path", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(main, "get_astrbot_plugin_path", lambda: str(root))
    context = SimpleNamespace(get_all_stars=lambda: [])

    async def run():
        p = main.GithookPlugin(context, {"watch_source": "workspace"})
        try:
            await p.refresh()
            assert len(p.plugins) == 9
            assert all(not x.installed for x in p.plugins)
            assert "新增插件检测需恢复清单连接" in p.catalog_status
            assert "快照" in p.catalog_status
            assert "astrbot_plugin_rt_link" not in {x.name for x in p.plugins}
        finally:
            await p.terminate()
        other = main.GithookPlugin(
            context, {"watch_source": "workspace", "workspace_repo": "Other/workspace"}
        )
        try:
            await other.refresh()
            assert other.plugins == []
            assert "无可用清单" in other.catalog_status
        finally:
            await other.terminate()

    asyncio.run(run())


def test_workspace_scan_timeout_keeps_the_verified_scope(monkeypatch, tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(main, "get_astrbot_data_path", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(main, "get_astrbot_plugin_path", lambda: str(root))
    monkeypatch.setattr(main, "WORKSPACE_SCAN_TIMEOUT", 0.02)

    async def blocked(settings):
        await asyncio.Future()

    async def run():
        p = main.GithookPlugin(
            SimpleNamespace(get_all_stars=lambda: []), {"watch_source": "workspace"}
        )
        p.github = SimpleNamespace(workspace_catalog=blocked)
        try:
            await asyncio.wait_for(p.refresh(), 1)
            assert len(p.plugins) == 9
            assert "核对超时" in p.catalog_status
        finally:
            await p.terminate()

    asyncio.run(run())
