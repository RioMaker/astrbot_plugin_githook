# githook

AstrBot 插件仓库自动发现、逐个勾选更新推送和 GitHub 提交历史。目录与插件标识统一为 `astrbot_plugin_githook`。

## 功能与权限

- 默认 `watch_source=owner`：自动发现 `owners` 指定账号或组织名下所有 `astrbot_plugin_` 开头且有后缀的 GitHub 仓库。无需在服务器安装目标插件，不检查目录名与 `metadata.name` 是否一致，不依赖工作区清单。账号默认 `RioMaker`。
- **每个仓库的推送开关默认关闭。** 自动发现仅向 AstrBot 配置面板“插件推送开关”添加关闭的选项；你手动勾选“开启该插件的更新推送”并保存后才会推送。以后新增仓库也保持关闭，既有勾选不会被扫描或重启重置。
- 配置开关以完整 `账号/仓库` 区分不同账号的同名插件。普通查询不受仓库开关限制。
- **每个群默认关闭主动推送，只有 AstrBot 管理员能在该群执行 `/githook 开` 或 `/githook 关`。** 单纯的 QQ 群主、群管理员不能操作。管理员身份来自 AstrBot 的 `event.is_admin()`。
- 普通用户可以主动查询。群开关不限制查询；私聊不能开启群推送。
- `/githook 六爻` 展示已安装插件的本机版本与状态；未安装目标显示远端登记信息，无法确定版本时显示“未知”。不把安装或登记信息冒称为最新发行版。
- 收到已勾选仓库的签名 `push` 后，只广播到开启的群。代码更新仍由 Webhook 驱动，不自动添加远端 Webhook，不自动更新插件。
- 首次扫描建立静默基线。新发现的仓库不推送“新增插件”提醒；只有此前明确手动开启的目标才可能提醒。重载、更新和已知插件重装不重复提醒。配置账号或监听来源变化会重新建立静默基线。
- 提交历史直接读取 GitHub API，可查安装 githook 以前的提交。默认查询各仓库默认分支；推送分支过滤与查询分支是独立设置。

## 命令

| 命令 | 含义 |
| --- | --- |
| `/githook`、`/githook 帮助` | 帮助 |
| `/githook 开`、`/githook 关` | 本群开启/关闭，限 AstrBot 管理员 |
| `/githook 状态` | 本群开关、发现/开启数、Webhook 最近投递和队列状态 |
| `/githook 列表` | 已发现插件及逐仓库推送开关，区分本机版本与登记信息 |
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

将插件安装到 AstrBot 的 `data/plugins/astrbot_plugin_githook/`，由 AstrBot 安装 `requirements.txt` 依赖。WebUI 保存配置会热重载插件。

1. 设置 `owners` 为你的 GitHub 用户名或组织名，可多个，默认 `RioMaker`。
2. 设置监听来源为 `owner`。升级自 0.1/0.2 时已有的 `installed` 或 `workspace` 值会保留，须手动改为 `owner`。等待扫描后重新打开或刷新配置面板，在“插件推送开关”勾选需要推送的仓库并保存。
3. 可选填写 GitHub 只读 `github_token`。公开仓库自动发现可以不填；多个仓库历史查询建议填写以提高额度。自动列出私有仓库需要 Token 可读目标仓库的 Metadata，读取提交需要 Contents: Read；无需写权限。GitHub App 的短期 installation token 不支持 `/user/repos`，列表可能只包含公开仓库与有效签名 push 已发现的目标。配置 Token 后，有权查询的用户也可看到被管理私有仓库的提交标题。
4. 配置随机 `webhook_secret`。所有受管仓库的 Webhook 采用相同 Secret。
5. 默认监听 `127.0.0.1:8765/githook/webhook`，通过 HTTPS 反向代理让 GitHub 可达。Docker 使用端口映射时，将 `webhook_host` 设置为 `0.0.0.0`。这是独立端口，不是 AstrBot WebUI 的插件 API。
6. 配置 GitHub App 的 `push` Webhook 并确保安装范围覆盖需要的仓库及未来仓库；或为每个目标仓库单独配置 `application/json`、相同 Secret 的 push Webhook。仓库自动发现不会代替 GitHub 的投递配置。
7. AstrBot 管理员在目标群发送 `/githook 开`。仓库勾选和群开关都开启后，后续代码更新才会发送。

## 自动发现与默认关闭

公开仓库列表使用 GitHub API 自动扫描，包含用户和组织，并处理每页 100 条的分页；有 Token 时补充其可读的目标账号仓库。默认每 60 秒复查，API 缓存 5 分钟。一个 `owners` 账号扫描失败不会阻止其他账号；状态会显示失败或旧缓存，已发现的仓库及其手动选择会保留。

仓库从扫描或通过验证的分支 push 中首次出现时，会添加以下配置项：

```json
{
  "repository_switches": [
    {
      "__template_key": "repository",
      "repo": "RioMaker/astrbot_plugin_example",
      "enabled": false
    }
  ]
}
```

在 WebUI 中展开该仓库选项、勾选开启并保存即可。普通的有效 push 不会自动勾选；签名错误、其他账号、其他前缀或无效提交不会创建选项。自动追加只更新本插件配置，不修改其他插件配置、代码或权限。

2026-10-06 使用本插件实际客户端无 Token 扫描 `RioMaker`，识别出 13 个符合前缀的公开仓库，包括 `grok`、`rp_taikover`、`rt_link` 和 `wuxing_num`。此数量是验证当时的状态；后续仓库变化会重新扫描。所有新增开关默认关闭。

关闭时收到的有效投递仍保存 Delivery ID 和提交缓存，但不创建通知队列。勾选开启后不补发旧投递；重复投递不会因为后来打开开关而重放。取消勾选会取消该仓库待发通知，之后重新勾选也不恢复它们。仓库被删除或改名时既有配置项会保留；新仓库名作为新选项默认关闭，旧项可保持关闭。

## 兼容的监听来源

`installed` 保留已安装自有插件识别。通常从 `metadata.repo`、`.git/config` 的 origin 或 `repo_overrides` 取得仓库；`owner` 模式识别安装状态时优先真实 origin，避免旧元数据地址产生错误关联。

`workspace` 保留 0.2.0 的工作区清单筛选，要求目录、仓库名与发布元数据一致，私有基准仓库需要可读 Token。附带快照可用 `.venv/Scripts/python tools/export_workspace_snapshot.py` 重新生成。默认 `owner` 模式不读取这份清单或快照，也不要求维护它来发现新仓库。

这三个来源都受仓库勾选和群开关控制，默认不为任何新仓库开启通知。

不配置 Webhook 仍可自动扫描仓库并查询历史。Secret 留空不监听端口；端口占用会使初始化失败并清理已创建资源。

可选设置：`branches` 控制播报分支（空=全部）；`history_branch` 控制查询分支（空=各仓库默认分支）；`scan_interval` 默认 60 秒；`max_push_commits` 默认 5。

## 状态与投递语义

持久数据位于 AstrBot 的 `data/plugin_data/astrbot_plugin_githook/githook.db`，包含群订阅、安装清单、提交缓存、HTTP 查询缓存、Delivery ID 和逐群通知队列。不会写入其他插件，不读取它们的配置或导入它们的 Python 代码。

Webhook 验证 HMAC-SHA256，限制请求体为 2 MiB。支持 ping、分支 push、分支删除和强制推送提示；标签及其他事件忽略。`owner` 模式通过账号与前缀检查后可即时登记未知仓库，仍默认关闭；不等待远端扫描，以避免 GitHub 投递超时。

| HTTP/状态 | 含义 |
| --- | --- |
| `200 ok` | ping 成功 |
| `202 queued` | 已持久化排队，不代表 QQ 已收到 |
| `202 no_subscribers` | 记录提交，但没有开启的群；未来开群不回补 |
| `200 ignored` | 仓库关闭（repository disabled）、重复投递、其他事件、仓库/分支不在范围 |
| `400` | 缺失 Delivery ID、JSON/提交字段无效 |
| `401` | 签名错误 |
| `409` | 同一个 Delivery ID 对应不同内容 |
| `413` | 超出请求体上限 |

各群单独记录发送结果，失败按退避策略尝试，最多 8 次，耗尽可在 `/githook 状态` 看到。关闭群或仓库会取消未发送通知。升级旧数据库时，为投递记录补充仓库字段；无法确定仓库的旧待发通知取消，避免绕过新开关。重启保留有效队列、手动选择和去重记录，已发送目标不因其他群失败而重发。采用至少一次投递：进程恰好在平台接收后、本地标记成功前崩溃时，可能重复一条消息。

单进程使用；不要让多个 AstrBot 实例共用同一数据库和端口。已完成的 Delivery ID 保留 90 天；提交缓存保留最多 50000 条，API 缓存最多 512 项/7 天。历史查询仍可访问 GitHub 更早的记录，缓存清理不删除 GitHub 数据。

## 开发验证

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest ruff
.venv/Scripts/python -m pytest -q -p no:cacheprovider
.venv/Scripts/ruff check .
.venv/Scripts/ruff format --check .
```

测试覆盖全关闭、手动勾选、自动追加不覆盖选择、取消队列、不回补、重启、用户/组织/私有仓库分页，以及权限、签名、历史、重试和资源清理。AstrBot 入口使用框架桩；本地通过不等同于线上 AstrBot/QQ 验收。

参考资料：[AstrBot 插件清单接口](https://docs.astrbot.app/dev/star/guides/other.html)、[存储约定](https://docs.astrbot.app/dev/star/guides/storage.html)、[GitHub 提交列表 API](https://docs.github.com/en/rest/commits/commits#list-commits)、[GitHub Webhook 类型](https://docs.github.com/en/webhooks/types-of-webhooks)、[Contents API](https://docs.github.com/en/rest/repos/contents#get-repository-content)。设计参考用户提供的本地 `nonebot-plugin-github-webhook-qq`，本插件独立实现 AstrBot 适配、查询和持久队列，没有复制该未附许可证项目的源文件。本目录暂不声明发布许可证。
