"""Validated settings; credentials are never included in status output."""

import json
from dataclasses import dataclass, field


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
        ):
            if key in values:
                values[key] = str(values[key]).strip()
        result = cls(**values)
        if not result.webhook_path.startswith("/") or any(c in result.webhook_path for c in "?#{}"):
            raise ValueError("webhook_path 必须是以 / 开头的固定路径")
        return result
