import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web
from astrbot_plugin_githook.catalog import PluginInfo
from astrbot_plugin_githook.config import Settings
from astrbot_plugin_githook.github import GitHubClient, format_history
from astrbot_plugin_githook.query import parse_history
from astrbot_plugin_githook.webhook import Webhook


@asynccontextmanager
async def server(app):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            pass
        else:
            writer.close()
            await writer.wait_closed()
            raise AssertionError("task-owned test port not released")


def plugin(name="liuyao"):
    return PluginInfo(name, name, "1", "RioMaker/" + name, "已启用")


def push(**updates):
    payload = {
        "repository": {"full_name": "RioMaker/liuyao"},
        "ref": "refs/heads/main",
        "pusher": {"name": "Rio"},
        "commits": [
            {
                "id": "a" * 40,
                "message": "feat: 历史\nbody",
                "timestamp": "2026-09-24T10:00:00+08:00",
            }
        ],
    }
    payload.update(updates)
    return payload


def test_webhook_real_http_signature_filter_dedup_and_queue(store):
    async def run():
        settings = replace(Settings(), webhook_secret="test-only-secret", branches=("main",))
        event = asyncio.Event()
        refreshes = []

        async def refresh():
            refreshes.append(True)

        handler = Webhook(settings, store, lambda: [plugin()], refresh, event)
        app = web.Application(client_max_size=2 * 1024 * 1024)
        app.router.add_post("/hook", handler.handle)
        store.set_enabled("one:GroupMessage:1", True)
        async with server(app) as base, aiohttp.ClientSession() as session:

            async def post(payload, delivery="id-1", name="push", bad=False):
                body = json.dumps(payload).encode()
                sig = (
                    "sha256="
                    + hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
                )
                headers = {
                    "X-Hub-Signature-256": "bad" if bad else sig,
                    "X-GitHub-Delivery": delivery,
                    "X-GitHub-Event": name,
                }
                async with session.post(base + "/hook", data=body, headers=headers) as r:
                    text = await r.text()
                    return r.status, json.loads(text) if r.status < 300 else text

            assert (await post(push(), bad=True))[0] == 401
            assert refreshes == []
            assert (await post({}, name="ping"))[1]["status"] == "ok"
            assert (await post({}, name="issues"))[1]["status"] == "ignored"
            assert (await post([]))[0] == 400
            assert (await post(push(), delivery=""))[0] == 400
            assert (await post(push(repository={"full_name": "Other/liuyao"})))[1][
                "status"
            ] == "ignored"
            assert (await post(push(repository={"full_name": "RioMaker/not_installed"})))[1][
                "status"
            ] == "ignored"
            assert (await post(push(ref="refs/tags/v1")))[1]["status"] == "ignored"
            assert (await post(push(ref="refs/heads/dev")))[1]["status"] == "ignored"
            assert (await post(push(commits=[{"id": "bad"}])))[0] == 400
            results = await asyncio.gather(post(push()), post(push()))
            assert sorted(r[1]["status"] for r in results) == ["ignored", "queued"]
            assert len(store.pending()) == 1
            assert "feat: 历史" in store.pending()[0]["message"]
            assert event.is_set()
            assert (await post(push(forced=True)))[0] == 409
            assert (await post(push(deleted=True, commits=[]), delivery="delete-2"))[0] == 202
            assert "分支已删除" in store.pending()[1]["message"]
            store.set_enabled("one:GroupMessage:1", False)
            assert (await post(push(), delivery="id-3"))[1]["status"] == "no_subscribers"
            async with session.post(base + "/hook", data=b"x" * (2 * 1024 * 1024 + 1)) as r:
                assert r.status == 413

    asyncio.run(run())


def test_github_real_http_pagination_aggregate_dates_cache_and_failures(store):
    async def run():
        requests = []
        mode = {"error": 0}

        async def commits(request):
            requests.append(dict(request.query))
            if mode["error"]:
                return web.Response(status=mode["error"])
            repo = request.match_info["repo"]
            start = datetime(2026, 9, 24, tzinfo=timezone.utc)
            entries = []
            for i in range(205):
                instant = start - timedelta(minutes=2 * i + (1 if repo == "b" else 0))
                stamp = instant.isoformat()
                if request.query.get("since") and stamp < request.query["since"]:
                    continue
                if request.query.get("until") and stamp > request.query["until"]:
                    continue
                entries.append(
                    {
                        "sha": f"{i:040x}",
                        "commit": {"message": f"{repo}-{i}\nbody", "committer": {"date": stamp}},
                    }
                )
            page = int(request.query.get("page", 1))
            return web.json_response(entries[(page - 1) * 100 : page * 100])

        app = web.Application()
        app.router.add_get("/repos/RioMaker/{repo}/commits", commits)
        async with (
            server(app) as base,
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session,
        ):
            client = GitHubClient(session, store, api_base=base)
            result = await client.history([plugin("a"), plugin("b")], parse_history(""))
            assert len(result.rows) == 30
            assert [r["title"] for r in result.rows[:4]] == ["a-0", "b-0", "a-1", "b-1"]
            assert not result.warnings
            result = await client.history([plugin("a")], parse_history("121-150"))
            assert len(result.rows) == 30
            assert result.rows[0]["title"] == "a-120"
            assert result.rows[-1]["title"] == "a-149"
            assert any(r["page"] == "2" for r in requests)
            q = parse_history("2026-09-24")
            result = await client.history([plugin("a")], q, branch="release")
            assert requests[-1]["since"] == "2026-09-23T16:00:00Z"
            assert requests[-1]["until"] == "2026-09-24T15:59:59Z"
            assert requests[-1]["sha"] == "release"
            assert "2026-09-24 08:00:00" in format_history(result, q, "a")
            count = len(requests)
            await client.history([plugin("a")], q, branch="release")
            assert len(requests) == count
            store.db.execute("UPDATE api_cache SET saved=?", (time.time() - 600,))
            mode["error"] = 500
            result = await client.history([plugin("a")], q, branch="release")
            assert len(result.rows) == 30
            assert "旧缓存" in result.warnings[0]
            mode["error"] = 404
            result = await client.history([plugin("unknown")], q)
            assert result.rows == []
            assert "不存在" in result.warnings[0]
            mode["error"] = 429
            result = await client.history([plugin("uncached")], q)
            assert "限流" in result.warnings[0]
            count = len(requests)
            await client.history([plugin("more")], q)
            assert len(requests) == count

    asyncio.run(run())
