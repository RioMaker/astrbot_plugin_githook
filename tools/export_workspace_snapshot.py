"""Export a verified offline snapshot from the actual independent workspace repos."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

from astrbot_plugin_githook.catalog import (  # noqa: E402
    repo_name,
    workspace_candidates,
    workspace_info,
)


def git(folder, *args):
    return subprocess.check_output(["git", "-C", str(folder), *args], encoding="utf-8").strip()


def export(workspace, output):
    workspace = workspace.resolve()
    manifest = json.loads((workspace / "plugins.json").read_text(encoding="utf-8-sig"))
    source_repo = repo_name(git(workspace, "remote", "get-url", "origin"))
    if not source_repo:
        raise ValueError("工作区没有可识别的 GitHub origin")
    owners = (source_repo.split("/")[0],)
    plugins = []
    for entry, repo in workspace_candidates(manifest, owners):
        folder = workspace / entry["directory"]
        if Path(git(folder, "rev-parse", "--show-toplevel")).resolve() != folder.resolve():
            raise ValueError(f"{entry['directory']} 不是独立 Git 仓库")
        if repo_name(git(folder, "remote", "get-url", "origin")) != repo:
            raise ValueError(f"{entry['directory']} 的清单 origin 与实际仓库不一致")
        metadata = yaml.safe_load((folder / "metadata.yaml").read_text(encoding="utf-8"))
        info = workspace_info(entry, repo, metadata)
        if info:
            plugins.append(info.to_dict())
    data = {
        "source": {"repository": source_repo, "path": "plugins.json", "branch": ""},
        "plugins": plugins,
    }
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已导出 {len(plugins)} 个规范命名插件到 {output}")
    for plugin in plugins:
        print(plugin["repo"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=PLUGIN_ROOT.parent)
    parser.add_argument("--output", type=Path, default=PLUGIN_ROOT / "workspace_snapshot.json")
    args = parser.parse_args()
    export(args.workspace, args.output)
