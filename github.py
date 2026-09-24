"""GitHub read API with pagination, bounded caching, and explicit stale results."""

import asyncio
import json
import time
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from .query import BEIJING, clean_title, parse_time


class GitHubError(Exception):
    pass


@dataclass
class HistoryResult:
    rows: list
    warnings: list[str]


class GitHubClient:
    def __init__(self, session, store, token="", api_base="https://api.github.com"):
        self.session, self.store, self.token = session, store, token
        self.api_base = api_base.rstrip("/")
        self.lock = asyncio.Lock()
        self.blocked_until = 0.0

    async def get(self, path, params=None):
        key = path + "?" + json.dumps(params or {}, sort_keys=True)
        async with self.lock:
            cached = self.store.cache_get(key)
            if cached and time.time() - cached[1] < 300:
                return cached[0], False
            try:
                if time.time() < self.blocked_until:
                    raise GitHubError("GitHub 请求额度暂不可用，请稍后查询")
                headers = {
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2026-03-10",
                    "User-Agent": "AstrBot-githook/0.1.0",
                }
                if self.token:
                    headers["Authorization"] = "Bearer " + self.token
                async with self.session.get(
                    self.api_base + path, params=params, headers=headers, allow_redirects=False
                ) as response:
                    if response.status in {403, 429}:
                        try:
                            reset = float(response.headers.get("X-RateLimit-Reset", "0"))
                            retry = float(response.headers.get("Retry-After", "60"))
                        except ValueError:
                            reset, retry = 0, 60
                        self.blocked_until = max(time.time() + max(60, retry), reset)
                        raise GitHubError(
                            "GitHub 拒绝请求或已限流，请检查只读 Token/仓库权限后重试"
                        )
                    if response.status == 401:
                        raise GitHubError("GitHub Token 无效，请管理员检查配置")
                    if response.status == 404:
                        raise GitHubError("仓库/分支不存在，或 Token 无权读取")
                    if response.status == 409:
                        return [], False  # Empty Git repository.
                    if response.status != 200:
                        raise GitHubError(f"GitHub 请求失败（HTTP {response.status}）")
                    data = await response.json(content_type=None)
                self.store.cache_put(key, data)
                return data, False
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, GitHubError) as exc:
                if cached and time.time() - cached[1] <= 7 * 86400:
                    return cached[0], True
                if isinstance(exc, GitHubError):
                    raise
                raise GitHubError("GitHub 暂时无法访问，请稍后重试") from None

    async def commits(self, repo, query, branch=""):
        rows, stale = [], False
        for page in range(1, (query.end + 99) // 100 + 1):
            params = {"per_page": 100, "page": page}
            if branch:
                params["sha"] = branch
            if query.since:
                params.update(since=query.since, until=query.until)
            payload, old = await self.get(f"/repos/{quote(repo, safe='/')}/commits", params)
            if not isinstance(payload, list):
                raise GitHubError("GitHub 返回了无效的提交列表")
            stale |= old
            for item in payload:
                try:
                    commit = item["commit"]
                    timestamp = commit.get("committer", {}).get("date") or commit.get(
                        "author", {}
                    ).get("date")
                    normalized = parse_time(timestamp).isoformat()
                    rows.append(
                        {
                            "repo": repo,
                            "branch": branch,
                            "sha": item["sha"],
                            "title": clean_title(commit["message"]),
                            "time": normalized,
                        }
                    )
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise GitHubError("GitHub 返回的提交缺少标题或时间") from None
            if len(payload) < 100:
                break
        self.store.cache_commits(rows)
        return rows, stale

    async def history(self, plugins, query, branch=""):
        rows, warnings = [], []
        repos = sorted({p.repo for p in plugins}, key=str.casefold)
        for repo in repos:
            try:
                commits, stale = await self.commits(repo, query, branch)
                rows.extend(commits)
                if stale:
                    warnings.append(f"{repo} 使用旧缓存（最长 7 天），可能缺少新提交")
            except GitHubError as exc:
                warnings.append(f"{repo}：{exc}")
        dedup = {(r["repo"].casefold(), r["sha"]): r for r in rows}
        ordered = sorted(
            dedup.values(),
            key=lambda r: (parse_time(r["time"]), r["repo"].casefold(), r["sha"]),
            reverse=True,
        )
        return HistoryResult(ordered[query.start - 1 : query.end], warnings)


def format_history(result, query, label):
    header = f"[githook 提交历史] {label}\n第 {query.start}–{query.end} 条 · 时间为北京时间（提交者时间）"
    if query.since:
        header += f"\n日期：{parse_time(query.since).astimezone(BEIJING):%Y-%m-%d} 至 {parse_time(query.until).astimezone(BEIJING):%Y-%m-%d}"
    lines = [header]
    if result.warnings:
        lines.append(
            "查询提示（结果可能不完整，汇总序号也可能变化）：\n" + "\n".join(result.warnings)
        )
    if not result.rows:
        lines.append("本次未返回提交。" if result.warnings else "所选范围没有提交。")
    for i, row in enumerate(result.rows, query.start):
        timestamp = parse_time(row["time"]).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(
            f"{i}. [{row['repo'].split('/')[-1]}] {row['title']}\n   {timestamp} · {row['sha'][:7]}"
        )
    if result.rows:
        target = f" {query.plugin}" if query.plugin else ""
        dates = ""
        if query.since:
            dates = f" {parse_time(query.since).astimezone(BEIJING):%Y-%m-%d}..{parse_time(query.until).astimezone(BEIJING):%Y-%m-%d}"
        if query.end < 3000:
            lines.append(
                f"继续：/githook 历史{target}{dates} {query.end + 1}-{min(3000, query.end + 30)}"
            )
        else:
            lines.append("更早的记录请缩小日期范围后查询。")
    return "\n".join(lines)
