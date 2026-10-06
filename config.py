"""Validated settings; credentials are never included in status output."""

import json
from dataclasses import dataclass, field

from .catalog import repo_name


def string_list(value):
    if isinstance(value, str):
        value = value.replace("，", ",").split(",")
    if not isinstance(value, (tuple, list)):
        raise ValueError("账号和分支配置必须是列表")
    return tuple(dict.fromkeys(str(x).strip() for x in value if str(x).strip()))


def mapping(value):
    if isinstance(value, str):
        value = json.loads(value or "{}")
    if not isinstance(value, dict):
        raise ValueError("别名和仓库映射必须是 JSON 对象")
    return {str(k).strip(): str(v).strip() for k, v in value.items()}


def repository_switches(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("插件推送开关必须是配置列表")
    rows, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("enabled", False), bool):
            raise ValueError("每个插件推送开关须包含仓库和布尔值 enabled")
        repo = repo_name(item.get("repo", ""))
        enabled = item.get("enabled", False)
        if not repo and not str(item.get("repo", "")).strip() and not enabled:
            continue
        if not repo:
            raise ValueError("插件推送开关的仓库须为 GitHub 账号/仓库名")
        if repo.casefold() in seen:
            raise ValueError("插件推送开关包含重复仓库")
        seen.add(repo.casefold())
        rows.append({"__template_key": "repository", "repo": repo, "enabled": enabled})
    return rows


@dataclass(frozen=True)
class Settings:
    owners: tuple[str, ...] = ("RioMaker",)
    aliases: dict[str, str] = field(default_factory=dict)
    repo_overrides: dict[str, str] = field(default_factory=dict)
    branches: tuple[str, ...] = ()
    history_branch: str = ""
    github_token: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8765
    webhook_path: str = "/githook/webhook"
    scan_interval: int = 60
    max_push_commits: int = 5
    watch_source: str = "owner"
    repository_switches: tuple = ()
    workspace_repo: str = "RioMaker/Astrbot_plguin_dev"
    workspace_manifest_path: str = "plugins.json"
    workspace_branch: str = ""

    def repo_enabled(self, repo):
        return any(
            item["repo"].casefold() == repo.casefold() and item["enabled"]
            for item in self.repository_switches
        )

    @classmethod
    def load(cls, config):
        values = {}
        for key in cls.__dataclass_fields__:
            if key in config:
                values[key] = config[key]
        for key in ("owners", "branches"):
            if key in values:
                values[key] = string_list(values[key])
        for key in ("aliases", "repo_overrides"):
            if key in values:
                values[key] = mapping(values[key])
        if "repository_switches" in values:
            values["repository_switches"] = tuple(
                repository_switches(values["repository_switches"])
            )
        for key, low, high in (
            ("webhook_port", 1, 65535),
            ("scan_interval", 10, 3600),
            ("max_push_commits", 1, 30),
        ):
            if key in values:
                values[key] = int(values[key])
                if not low <= values[key] <= high:
                    raise ValueError(f"{key} 必须在 {low}–{high} 之间")
        for key in (
            "github_token",
            "webhook_secret",
            "webhook_host",
            "webhook_path",
            "history_branch",
            "watch_source",
            "workspace_repo",
            "workspace_manifest_path",
            "workspace_branch",
        ):
            if key in values:
                values[key] = str(values[key]).strip()
        result = cls(**values)
        if result.watch_source not in {"owner", "installed", "workspace"}:
            raise ValueError("watch_source 必须是 owner、installed 或 workspace")
        if result.watch_source == "workspace":
            if repo_name(result.workspace_repo) != result.workspace_repo:
                raise ValueError("workspace_repo 必须是 GitHub 账号/仓库名")
            parts = result.workspace_manifest_path.split("/")
            if any(p in {"", ".", ".."} for p in parts) or any(
                c in result.workspace_manifest_path for c in "\\?#{}"
            ):
                raise ValueError("workspace_manifest_path 必须是仓库内的相对文件路径")
        if not result.webhook_path.startswith("/") or any(c in result.webhook_path for c in "?#{}"):
            raise ValueError("webhook_path 必须是以 / 开头的固定路径")
        return result
