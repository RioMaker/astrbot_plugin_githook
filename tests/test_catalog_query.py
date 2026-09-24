import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from astrbot_plugin_githook.catalog import PluginInfo, discover, repo_name, resolve
from astrbot_plugin_githook.config import Settings
from astrbot_plugin_githook.query import chunks, parse_history


def plugin(name="liuyao", repo=None):
    return PluginInfo(
        "astrbot_plugin_" + name,
        "六爻" if name == "liuyao" else name,
        "1.0",
        repo or "RioMaker/" + name,
        "已启用",
    )


def installed(root, name="liuyao", owner="RioMaker", **extra):
    folder = root / ("astrbot_plugin_" + name)
    folder.mkdir(exist_ok=True)
    metadata = {
        "name": folder.name,
        "repo": f"https://github.com/{owner}/{folder.name}",
        "author": "Rio",
        "version": "0.9.0",
        "display_name": "六爻起卦" if name == "liuyao" else name,
    }
    metadata.update(extra)
    (folder / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True), encoding="utf-8"
    )
    return folder


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://github.com/RioMaker/test.git", "RioMaker/test"),
        ("git@github.com:RioMaker/test.git", "RioMaker/test"),
        ("ssh://git@github.com/RioMaker/test", "RioMaker/test"),
        ("https://github.com.evil/RioMaker/test", ""),
        ("https://evil/RioMaker/test", ""),
        ("RioMaker/../private", ""),
        ("RioMaker/..", ""),
    ],
)
def test_repository_normalization(value, expected):
    assert repo_name(value) == expected


def test_owned_installed_only_and_runtime_state(tmp_path):
    installed(tmp_path)
    installed(tmp_path, "third_party", "other", author="Rio")
    installed(tmp_path, "no_repo", repo="")
    result = discover(
        tmp_path, [SimpleNamespace(name="astrbot_plugin_liuyao", activated=False)], Settings()
    )
    assert len(result) == 1
    assert result[0].status == "已禁用"
    assert result[0].version == "0.9.0"
    assert resolve(result, "六爻").name == "astrbot_plugin_liuyao"
    result = discover(tmp_path, [], Settings())
    assert "未载入" in result[0].status


def test_missing_repo_origin_and_override_still_require_owner(tmp_path):
    folder = installed(tmp_path, repo="")
    (folder / ".git").mkdir()
    (folder / ".git" / "config").write_text(
        '[remote "origin"]\nurl = https://github.com/RioMaker/liuyao.git\n', encoding="utf-8"
    )
    assert discover(tmp_path, [], Settings())[0].repo == "RioMaker/liuyao"
    settings = replace(Settings(), repo_overrides={"astrbot_plugin_liuyao": "other/liuyao"})
    assert discover(tmp_path, [], settings) == []


def test_alias_and_ambiguous_names():
    plugins = [plugin(), plugin("liuyao_extra")]
    assert resolve(plugins, "六爻", {"六爻": "astrbot_plugin_liuyao"}) == plugins[0]
    with pytest.raises(ValueError, match="不唯一"):
        resolve(plugins, "liu")
    with pytest.raises(ValueError, match="未找到"):
        resolve(plugins, "third_party")


@pytest.mark.parametrize(
    "text,plugin_name,start,end",
    [
        ("", "", 1, 30),
        ("六爻", "六爻", 1, 30),
        ("六爻 页 2", "六爻", 31, 60),
        ("六爻 3", "六爻", 61, 90),
        ("第2页", "", 31, 60),
        ("六爻 61-90", "六爻", 61, 90),
        ("六爻 1-100", "六爻", 1, 100),
    ],
)
def test_history_syntax(text, plugin_name, start, end):
    q = parse_history(text)
    assert (q.plugin, q.start, q.end) == (plugin_name, start, end)


def test_dates_include_beijing_whole_day_and_pagination():
    q = parse_history("六爻 2026-09-01..2026-09-24 页 2")
    assert q.since == "2026-08-31T16:00:00Z"
    assert q.until == "2026-09-24T15:59:59Z"
    assert q.start == 31
    assert parse_history("2026-09-01 2026-09-24").until == q.until
    assert parse_history("2026-09-01").until == "2026-09-01T15:59:59Z"


@pytest.mark.parametrize(
    "text",
    [
        "0",
        "页 x",
        "1-101",
        "3001-3030",
        "60-30",
        "六爻 页 2 61-90",
        "2026-02-30",
        "2026-09-24..2026-09-01",
        "六爻 unexpected",
        "页",
        "2100-01-01",
    ],
)
def test_invalid_ranges(text):
    with pytest.raises(ValueError):
        parse_history(text)


def test_chunk_does_not_lose_titles():
    text = "\n".join("提交" + str(i) + "x" * 1800 for i in range(30))
    parts = chunks(text)
    assert all(len(p) <= 1600 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_schema_defaults_load_and_secrets_not_repr():
    path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    result = Settings.load({k: v["default"] for k, v in schema.items()})
    assert result.aliases["六爻"] == "astrbot_plugin_liuyao"
    assert "private-secret" not in repr(replace(result, github_token="private-secret"))
