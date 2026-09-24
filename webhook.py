"""GitHub push validation and durable acceptance, independent from AstrBot."""

import hashlib
import hmac
import json
import re

from aiohttp import web

from .catalog import owned, repo_name
from .query import clean_title, parse_time


class Webhook:
    def __init__(self, settings, store, get_plugins, refresh, wake):
        self.settings, self.store = settings, store
        self.get_plugins, self.refresh, self.wake = get_plugins, refresh, wake

    async def handle(self, request):
        secret = self.settings.webhook_secret
        if not secret:
            raise web.HTTPServiceUnavailable(text="webhook secret not configured")
        body = await request.read()
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
            raise web.HTTPUnauthorized(text="invalid signature")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text="invalid JSON") from None
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="JSON object required")
        event = request.headers.get("X-GitHub-Event", "").lower()
        if event == "ping":
            return web.json_response({"status": "ok", "event": "ping"})
        if event != "push":
            return web.json_response({"status": "ignored", "reason": "unsupported event"})
        delivery = request.headers.get("X-GitHub-Delivery", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery):
            raise web.HTTPBadRequest(text="valid Delivery ID required")
        repository = payload.get("repository")
        if not isinstance(repository, dict):
            raise web.HTTPBadRequest(text="repository object required")
        repo = repo_name(repository.get("full_name", ""))
        if not owned(repo, self.settings.owners):
            return web.json_response({"status": "ignored", "reason": "owner not managed"})
        await self.refresh()
        plugins = [p for p in self.get_plugins() if p.repo.casefold() == repo.casefold()]
        if not plugins:
            return web.json_response(
                {"status": "ignored", "reason": "plugin not installed or not managed"}
            )
        ref = payload.get("ref", "")
        if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
            return web.json_response({"status": "ignored", "reason": "not a branch push"})
        branch = ref[len("refs/heads/") :]
        if self.settings.branches and branch not in self.settings.branches:
            return web.json_response({"status": "ignored", "reason": "branch not allowed"})
        raw = payload.get("commits", [])
        if not isinstance(raw, list) or not all(isinstance(c, dict) for c in raw):
            raise web.HTTPBadRequest(text="invalid commits")
        commits = []
        for c in raw:
            try:
                sha = str(c["id"])
                if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
                    raise ValueError("invalid sha")
                commits.append(
                    {
                        "repo": repo,
                        "branch": branch,
                        "sha": sha,
                        "title": clean_title(c.get("message")),
                        "time": parse_time(c["timestamp"]).isoformat(),
                    }
                )
            except (KeyError, TypeError, ValueError):
                raise web.HTTPBadRequest(text="invalid commit id or timestamp") from None
        pusher = payload.get("pusher") or {}
        user = clean_title(pusher.get("name", "未知")) if isinstance(pusher, dict) else "未知"
        action = "分支已删除" if payload.get("deleted") else "代码更新"
        if payload.get("forced"):
            action += "（强制推送）"
        lines = [
            f"[githook {action}] {' / '.join(p.display_name for p in plugins)}",
            f"仓库：{repo}",
            f"分支：{branch}",
            f"推送者：{user}",
            f"本次载荷包含 {len(commits)} 条提交",
        ]
        for commit in commits[: self.settings.max_push_commits]:
            lines.append(f"{commit['sha'][:7]} {commit['title']}")
        if len(commits) > self.settings.max_push_commits:
            lines.append(
                f"另有 {len(commits) - self.settings.max_push_commits} 条；用 /githook 历史 查询"
            )
        # Construct a trusted GitHub URL instead of broadcasting an arbitrary payload URL.
        lines.append(f"https://github.com/{repo}")
        try:
            created = self.store.record_push(
                "github:" + delivery, hashlib.sha256(body).hexdigest(), "\n".join(lines), commits
            )
        except ValueError:
            raise web.HTTPConflict(text="delivery content mismatch") from None
        self.wake.set()
        if not created:
            return web.json_response({"status": "ignored", "reason": "duplicate delivery"})
        return web.json_response(
            {"status": "queued" if self.store.targets() else "no_subscribers"}, status=202
        )
