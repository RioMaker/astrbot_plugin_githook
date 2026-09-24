"""Read installed metadata and runtime state without importing other plugins."""

import configparser
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def repo_name(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:") :]
    elif "://" in value:
        parsed = urlsplit(value)
        if parsed.hostname != "github.com" or parsed.scheme not in {"https", "http", "ssh"}:
            return ""
        value = parsed.path.lstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value):
        return ""
    if value.split("/")[1] in {".", ".."}:
        return ""
    return value


def owned(repo, owners):
    return bool(repo) and repo.split("/")[0].casefold() in {x.casefold() for x in owners}


def field(obj, key, default=""):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


@dataclass(frozen=True)
class PluginInfo:
    name: str
    display_name: str
    version: str
    repo: str
    status: str
    description: str = ""

    def to_dict(self):
        return asdict(self)


def git_origin(folder):
    # Only a regular .git/config is inspected; no commands or linked worktree paths.
    path = folder / ".git" / "config"
    if not path.is_file() or path.is_symlink():
        return ""
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(path, encoding="utf-8")
        return config.get('remote "origin"', "url", fallback="")
    except (OSError, configparser.Error):
        return ""


def discover(root: Path, stars: list, settings) -> list[PluginInfo]:
    """Include disabled/unloaded installed plugins, but never infer ownership by author label."""
    root = root.resolve()
    runtime = {}
    for star in stars:
        for key in (field(star, "root_dir_name"), field(star, "name")):
            if key:
                runtime[key] = star
    result = {}
    if not root.is_dir():
        raise OSError("AstrBot 插件目录不可读")
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent != root:
            continue
        path = next(
            (folder / f for f in ("metadata.yaml", "metadata.yml") if (folder / f).is_file()), None
        )
        if path is None or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 128 * 1024:
                continue
            metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if not isinstance(metadata, dict):
            continue
        name = str(metadata.get("name") or folder.name)
        raw_repo = settings.repo_overrides.get(name) or settings.repo_overrides.get(folder.name)
        repo = repo_name(raw_repo or metadata.get("repo") or git_origin(folder))
        if not owned(repo, settings.owners):
            continue
        star = runtime.get(folder.name) or runtime.get(name)
        if star is None:
            status = "已安装，未载入（可能加载失败）"
        elif not field(star, "activated", True):
            status = "已禁用"
        elif field(star, "star_cls", None) is None:
            status = "载入中或未完成初始化"
        else:
            status = "已启用"
        result[name] = PluginInfo(
            name=name,
            display_name=str(metadata.get("display_name") or name),
            version=str(metadata.get("version") or "未知"),
            repo=repo,
            status=status,
            description=str(metadata.get("short_desc") or metadata.get("desc") or ""),
        )
    return list(result.values())


def resolve(plugins, query: str, aliases=None):
    query = (aliases or {}).get(query, query).casefold()
    exact, fuzzy = [], []
    for plugin in plugins:
        keys = {
            plugin.name.casefold(),
            plugin.display_name.casefold(),
            plugin.repo.casefold(),
            plugin.repo.split("/")[-1].casefold(),
        }
        keys.add(plugin.name.removeprefix("astrbot_plugin_").casefold())
        if query in keys:
            exact.append(plugin)
        elif query and any(query in key for key in keys):
            fuzzy.append(plugin)
    matches = exact or fuzzy
    if not matches:
        raise ValueError(
            f"未找到受管插件“{query}”。用 /githook 列表 查看；仓库必须属于配置的 GitHub 账号。"
        )
    if len(matches) > 1:
        raise ValueError("名称不唯一，请使用完整插件名：\n" + "\n".join(x.name for x in matches))
    return matches[0]
