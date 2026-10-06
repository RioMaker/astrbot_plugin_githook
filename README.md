# githook

AstrBot 工作区插件更新通知、GitHub 提交历史和新增提醒。目录与插件标识统一为 `astrbot_plugin_githook`。

## 功能与权限

- `watch_source=installed` 默认保留原行为：管理已安装且 GitHub 仓库所属账号在 `owners` 中的插件。商店安装和手动安装采用同一识别规则。
- `watch_source=workspace` 严格依据工作区 `plugins.json`：目录名须符合 `astrbot_plugin_[a-z0-9_]+`，实际仓库名和发布的 `metadata.name` 须与目录名一致，仓库账号须在 `owners` 中。允许目标插件未安装在服务器；同账号、同前缀但不在工作区清单中的仓库也不会纳入。
- **每个群默认关闭主动推送，只有 AstrBot 管理员能在该群执行 `/githook 开` 或 `/githook 关`。** 单纯的 QQ 群主、群管理员不能操作。管理员身份来自 AstrBot 的 `event.is_admin()`。
- 普通用户可以主动查询。群开关不限制查询；私聊不能开启群推送。
- `/githook 六爻` 展示本机安装版本、加载/禁用状态、仓库和简介；不把本机版本冒称为 GitHub 最新版本。
- 收到监听清单中插件的签名 `push` 事件后，广播到开启的群。代码更新仍由 Webhook 驱动，不自动添加远端 Webhook，不自动更新插件。
- 首次扫描只建立基线。后续首次发现新增受管插件时，向当时开启的群提醒。重载、更新和已知插件重装不重复提醒。配置账号或工作区来源变化会重新建立静默基线。
- 提交历史直接读取 GitHub API，可查安装 githook 以前的提交。默认查询各仓库默认分支；推送分支过滤与查询分支是独立设置。

## 命令

| 命令 | 含义 |
| --- | --- |
| `/githook`、`/githook 帮助` | 帮助 |
| `/githook 开`、`/githook 关` | 本群开启/关闭，限 AstrBot 管理员 |
| `/githook 状态` | 本群开关、Webhook、队列状态 |
| `/githook 列表` | 当前监听范围的插件，区分本机安装版本与登记版本 |
| `/githook 六爻` | 查询六爻插件安装或远端登记信息 |
| `/githook 历史` | 汇总全部受管插件最近 30 条提交 |
| `/githook 历史 六爻` | 六爻最近 30 条提交 |
| `/githook 历史 六爻 页 2` | 第 31–60 条；也可写 `2` 或 `第2页` |
| `/githook 历史 六爻 61-90` | 第 61–90 条 |
| `/githook 历史 六爻 2026-09-01..2026-09-24` | 日期范围内最新 30 条 |
| `/githook 历史 2026-09-01 2026-09-24 页 2` | 全部插件在日期范围内的第 2 页 |
| `/githook 历史 六爻 2026-09-20` | 当日提交 |

查询别名支持完整标识、展示名、仓库名及唯一的部分名称；歧义会提示候选，不任意选择。`aliases` 可明确配置 `{"六爻":"astrbot_plugin_liuyao"}`。

默认每次 30 条，范围查询每次最多 100 条，条目上限 3000；更久以前的记录使用日期范围。日期以北京时间 UTC+8 解释，起止日均包含。显示 Git **提交者时间**（committer date），不是机器人收到通知的时间。汇总按提交者时间降序排列，相同 SHA 在不同仓库视为不同记录；新提交会使后续查询的页码位置变化。

长结果分段发送，保留请求范围内的标题和时间。GitHub 错误或限流会明确标注，部分仓库失败的汇总不宣称完整。相同请求缓存 5 分钟，网络失败时最多回退到 7 天旧缓存并显示提示。

## 安装与配置

适用 AstrBot >=4.24.0，平台声明为 QQ OneBot (`aiocqhttp`)。使用 AstrBot 的会话发送接口；尚未完成真实 QQ 联调。QQ 官方机器人有不同的主动消息能力，本版本没有移植参考实现的官方 QQ 专用发送接口。

将插件安装到 AstrBot 的 `data/plugins/astrbot_plugin_githook/`，由 AstrBot 安装 `requirements.txt` 依赖。修改 WebUI 配置后重载插件。

1. 设置 `owners` 为你的 GitHub 用户名或组织名，可多个，默认 `RioMaker`。
2. 确认各插件的 `metadata.yaml` 有真实的 `repo`。为空时尝试读取普通 `.git/config` 的 origin；ZIP 安装没有 `.git` 时，可在 `repo_overrides` 填 `{"插件标识":"账号/仓库"}`。映射仍受 owners 限制。仅凭作者显示名或文件夹名称不会自动猜仓库。
3. 可选填写 GitHub 只读 `github_token`。公开仓库可以不填，跨多个仓库查询建议填写以提高额度；私有仓库需要读取 Contents 的权限。配置 Token 后，有权使用查询命令的用户也可以看到被管理私有仓库的提交标题。
4. 配置随机 `webhook_secret`。所有受管仓库的 Webhook 采用相同 Secret。
5. 默认监听 `127.0.0.1:8765/githook/webhook`，通过 HTTPS 反向代理让 GitHub 可达。Docker 使用端口映射时，将 `webhook_host` 设置为 `0.0.0.0`。这是独立端口，不是 AstrBot WebUI 的插件 API。
6. 在每个需要播报的 GitHub 仓库 `Settings → Webhooks` 添加：Payload URL 为你的 HTTPS 地址，Content type 为 `application/json`，填写同一 Secret，选择 push 事件。新增插件会自动纳管和提醒，但其 GitHub Webhook 仍需仓库管理员配置。
7. AstrBot 管理员在目标群发送 `/githook 开`，再用 GitHub 的 ping/测试投递验证连接。

## 本工作区及以后新增插件的接入

服务器无法直接读取开发机目录。本工作区通过私有基准仓库 `RioMaker/Astrbot_plguin_dev` 的 `plugins.json` 发布允许监听的目录和真实 origin；本地未推送的清单不能被服务器发现。

更新 githook 到 0.2.0 后，在 WebUI 配置并重载：

```json
{
  "owners": ["RioMaker"],
  "watch_source": "workspace",
  "workspace_repo": "RioMaker/Astrbot_plguin_dev",
  "workspace_manifest_path": "plugins.json",
  "workspace_branch": "",
  "branches": []
}
```

上述内容只是需核对的配置项，保留已设的 Secret、监听地址、端口、Token 和群订阅。私有基准仓库需要 `github_token` 对该仓库拥有 **Contents: Read** 权限；若使用细粒度 Token，也应允许读取受管插件仓库的元数据和提交。无需写入仓库或管理 Webhook 的 Token 权限。

当前严格符合“目录名 = 仓库名 = metadata.name”的 9 个插件是：`access_gate`、`analysis`、`custom_help`、`ganzhi`、`githook`、`img_gener`、`liuyao`、`think_bubble`、`welcome_qqgroup`（均带 `astrbot_plugin_` 前缀）。`grok_uuapi` 的 origin 仓库名不一致，`junjunget` 的目录和标识不一致，`rt_link`、`wuxing_num` 的标识缺少前缀，`rp_taikover-master` 有下载后缀且标识不一致；这些暂不纳入，也不会自动重命名。

清单和各插件当前发布的 `metadata.yaml` 通过 GitHub Contents API 核对，缓存 5 分钟，扫描默认每 60 秒唤醒。读取失败会使用最多 7 天旧缓存；首次接入不可读时使用本包附带的 9 插件快照。`/githook 状态` 会明确显示降级和新增检测不可用，旧数据不会冒充刚核对的结果。离线快照仅适用于绑定的工作区来源，不能替代持续的私有清单读取。

以后创建插件时：

1. 按工作区规则保持目录、独立仓库和 `metadata.name` 三者一致，并将插件上传到 GitHub。
2. 完成插件本地提交和 `plugins.json` 同步后，在授权范围内将基准仓库清单也推送到 GitHub。
3. 已配置 **GitHub App** 时，确认其订阅 `push`，Webhook 地址和 Secret 正确，安装范围覆盖现在和未来的仓库。个人账号使用“所有仓库”安装范围时，新仓库可沿用同一 App 的 Webhook；githook 仍用工作区清单严格筛选。使用单仓库 Webhook 时仍须为新仓库配置 Webhook。
4. 清单缓存到期后的下一次扫描会纳入新插件并提醒已开启的群；以后 push 正常通知。新插件尚未纳入时的首次 push 不会回补，提交可用历史命令查询。

可重新生成附带快照：`.venv/Scripts/python tools/export_workspace_snapshot.py`。此工具只读取工作区及插件实际 Git 仓库，不修改其他插件或清单。

检查 `/githook 状态` 应看到 `workspace`、9 个受管插件和“工作区清单及插件命名已核对”。`/githook 列表` 中未安装目标也应出现。最近签名投递会显示 `queued`、`no_subscribers` 或 `ignored` 的具体原因；无效签名不会改变该诊断信息。

不需要配置 Webhook 即可查询历史和接收新增插件提醒。Secret 留空时不监听端口；端口占用会使初始化失败，并清理已创建的资源。

可选设置：`branches` 控制播报分支（空=全部）；`history_branch` 控制查询分支（空=各仓库默认分支）；`scan_interval` 默认 60 秒；`max_push_commits` 默认 5。

## 状态与投递语义

持久数据位于 AstrBot 的 `data/plugin_data/astrbot_plugin_githook/githook.db`，包含群订阅、安装清单、提交缓存、HTTP 查询缓存、Delivery ID 和逐群通知队列。不会写入其他插件，不读取它们的配置或导入它们的 Python 代码。

Webhook 验证 HMAC-SHA256，限制请求体为 2 MiB。支持 ping、分支 push、分支删除和强制推送提示；标签及其他事件忽略。只接受当前监听清单且仓库账号匹配的插件。工作区模式的请求使用最近完整清单，不等待远端扫描，以避免 GitHub 投递超时。

| HTTP/状态 | 含义 |
| --- | --- |
| `200 ok` | ping 成功 |
| `202 queued` | 已持久化排队，不代表 QQ 已收到 |
| `202 no_subscribers` | 记录提交，但没有开启的群；未来开群不回补 |
| `200 ignored` | 重复投递、其他事件、仓库/分支不在范围 |
| `400` | 缺失 Delivery ID、JSON/提交字段无效 |
| `401` | 签名错误 |
| `409` | 同一个 Delivery ID 对应不同内容 |
| `413` | 超出请求体上限 |

各群单独记录发送结果，失败按退避策略尝试，最多 8 次，耗尽可在 `/githook 状态` 看到。关闭群推送会取消未发送的通知；完成关闭回复后不会继续发送已有队列。重启保留队列和去重记录，已发送目标不因其他群失败而重发。采用至少一次投递：进程恰好在平台接收后、本地标记成功前崩溃时，可能重复一条消息，无法声称端到端恰好一次。

单进程使用；不要让多个 AstrBot 实例共用同一数据库和端口。已完成的 Delivery ID 保留 90 天；提交缓存保留最多 50000 条，API 缓存最多 512 项/7 天。历史查询仍可访问 GitHub 更早的记录，缓存清理不删除 GitHub 数据。

## 开发验证

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest ruff
.venv/Scripts/python -m pytest -q -p no:cacheprovider
.venv/Scripts/ruff check .
.venv/Scripts/ruff format --check .
```

测试覆盖权限、会话隔离、作者筛选、新增基线、查询参数与真实本地 HTTP 请求、签名、去重、分组重试、缓存降级和生命周期资源清理。AstrBot 入口使用框架桩；本地测试不等同于线上 AstrBot/QQ 验收。

参考资料：[AstrBot 插件清单接口](https://docs.astrbot.app/dev/star/guides/other.html)、[存储约定](https://docs.astrbot.app/dev/star/guides/storage.html)、[GitHub 提交列表 API](https://docs.github.com/en/rest/commits/commits#list-commits)、[GitHub Webhook 类型](https://docs.github.com/en/webhooks/types-of-webhooks)、[Contents API](https://docs.github.com/en/rest/repos/contents#get-repository-content)。设计参考用户提供的本地 `nonebot-plugin-github-webhook-qq`，本插件独立实现 AstrBot 适配、查询和持久队列，没有复制该未附许可证项目的源文件。本目录暂不声明发布许可证。
