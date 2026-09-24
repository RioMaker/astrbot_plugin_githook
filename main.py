"""AstrBot command/lifecycle adapter for githook."""

import asyncio
import contextlib
import json
from pathlib import Path

import aiohttp
from aiohttp import web
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_plugin_path

from .catalog import discover, field, resolve
from .config import Settings
from .github import GitHubClient, format_history
from .query import chunks, parse_history
from .store import Store
from .webhook import Webhook

HELP = """githook · 自有插件更新与提交历史
/githook 开 /githook 关：仅 AstrBot 管理员在群内操作
/githook 状态：本群推送、Webhook 和发送队列状态
/githook 列表：已安装且仓库属于指定账号的插件
/githook 六爻：匹配插件的本机版本、启用状态和仓库
/githook 历史：全部受管插件最近 30 条提交
/githook 历史 六爻：六爻最近 30 条
/githook 历史 六爻 页 2：第 31–60 条（也可直接写 2）
/githook 历史 六爻 61-90：指定条目范围
/githook 历史 六爻 2026-09-01..2026-09-24：日期范围
/githook 历史 2026-09-01 2026-09-24 页 2：汇总日期范围第 2 页
日期采用北京时间，范围两端均包含；单次最多 100 条。
历史来自 GitHub 默认分支（可配置），含安装前的提交。
开关只控制后续主动推送，不限制主动查询；不会回补开启前的通知。
"""


@register("astrbot_plugin_githook", "Rio", "自有插件 GitHub 推送、历史查询与新增安装提醒", "0.1.0")
class GithookPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.settings = Settings.load(config)
        self.store = Store(
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_githook" / "githook.db"
        )
        self.plugins = []
        self.scan_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.command_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.scan_wake = asyncio.Event()
        self.tasks = []
        self.session = None
        self.github = None
        self.runner = None
        self.webhook_status = "未启动"
        self.closed = False

    async def initialize(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.github = GitHubClient(self.session, self.store, self.settings.github_token)
        try:
            await self.refresh()
            if self.settings.webhook_secret:
                handler = Webhook(
                    self.settings, self.store, lambda: self.plugins, self.refresh, self.wake
                )
                app = web.Application(client_max_size=2 * 1024 * 1024)
                app.router.add_post(self.settings.webhook_path, handler.handle)
                self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
                await self.runner.setup()
                await web.TCPSite(
                    self.runner, self.settings.webhook_host, self.settings.webhook_port
                ).start()
                self.webhook_status = f"已监听 {self.settings.webhook_host}:{self.settings.webhook_port}{self.settings.webhook_path}"
            else:
                self.webhook_status = "未配置 Secret；历史查询和新增插件检测可用"
            self.tasks = [
                asyncio.create_task(self._scan_loop()),
                asyncio.create_task(self._sender_loop()),
            ]
        except Exception:
            await self.terminate()
            raise

    async def refresh(self):
        async with self.scan_lock:
            # Freeze mutable runtime metadata before yielding to the directory reader.
            stars = [
                {
                    key: field(s, key, None)
                    for key in ("root_dir_name", "name", "activated", "star_cls")
                }
                for s in self.context.get_all_stars()
            ]
            snapshot = await asyncio.to_thread(
                discover, Path(get_astrbot_plugin_path()), stars, self.settings
            )
            owner_key = json.dumps(sorted(o.casefold() for o in self.settings.owners))
            self.store.sync_plugins(snapshot, owner_key)
            self.plugins = snapshot
            self.wake.set()

    async def _scan_loop(self):
        while not self.closed:
            try:
                await asyncio.wait_for(self.scan_wake.wait(), self.settings.scan_interval)
            except asyncio.TimeoutError:
                pass
            self.scan_wake.clear()
            if self.closed:
                return
            try:
                await self.refresh()
                self.store.prune()
            except Exception as exc:
                logger.error(f"githook 插件扫描失败：{type(exc).__name__}")

    if hasattr(filter, "on_plugin_loaded"):

        @filter.on_plugin_loaded()
        async def on_plugin_loaded(self, plugin):
            self.scan_wake.set()

    if hasattr(filter, "on_plugin_unloaded"):

        @filter.on_plugin_unloaded()
        async def on_plugin_unloaded(self, plugin):
            self.scan_wake.set()

    async def _sender_loop(self):
        while not self.closed:
            self.wake.clear()
            try:
                for item in self.store.pending():
                    async with self.send_lock:
                        if not self.store.enabled(item["umo"]):
                            continue
                        try:
                            sent = await asyncio.wait_for(
                                self.context.send_message(
                                    item["umo"], MessageChain([Plain(item["message"])])
                                ),
                                timeout=20,
                            )
                            if sent is False:
                                raise RuntimeError("platform unavailable")
                        except Exception as exc:
                            self.store.failed(item)
                            logger.warning(
                                f"githook 推送失败，将按队列策略重试：{type(exc).__name__}"
                            )
                        else:
                            self.store.sent(item)
            except Exception as exc:
                logger.error(f"githook 发送队列异常：{type(exc).__name__}")
            try:
                await asyncio.wait_for(self.wake.wait(), 3)
            except asyncio.TimeoutError:
                pass

    @filter.command("githook")
    async def command(self, event: AstrMessageEvent, content: GreedyStr):
        """管理自有插件的群推送，查询状态和 GitHub 提交历史。"""
        # Permission and routing must be captured before the first await.
        text = str(content or "").strip()
        group = str(event.get_group_id() or "")
        umo = str(event.unified_msg_origin)
        user = str(event.get_sender_id())
        admin = bool(event.is_admin())
        event.stop_event()
        try:
            if text in {"开", "关"}:
                if not group:
                    answer = "请由 AstrBot 管理员在需要推送的群内使用 /githook 开 或 /githook 关。"
                elif not admin:
                    answer = "仅 AstrBot 管理员可以开关；QQ 群主或群管理员身份不具备此权限。"
                else:
                    # Snapshot before enabling so pre-existing installs are not replayed.
                    if text == "开":
                        await self.refresh()
                    async with self.send_lock:
                        self.store.set_enabled(umo, text == "开", user)
                    answer = (
                        "已开启本群 githook 推送：后续代码更新及自有插件新增安装会在本群提醒。"
                        if text == "开"
                        else "已关闭本群 githook 推送，尚未发送的通知已取消。"
                    )
                    if text == "开" and not self.settings.webhook_secret:
                        answer += (
                            "\nGitHub 更新推送还需在 WebUI 配置 Webhook Secret 并连接 GitHub。"
                        )
            elif not text or text in {"帮助", "help"}:
                answer = HELP
            elif text == "状态":
                counts = self.store.delivery_counts()
                answer = f"[githook 状态]\n本群推送：{'已开启' if group and self.store.enabled(umo) else '未开启'}\n受管插件：{len(self.plugins)} 个\n作者账号：{', '.join(self.settings.owners) or '未配置'}\nWebhook：{self.webhook_status}\n待发送：{counts.get('pending', 0)}；重试耗尽：{counts.get('failed', 0)}\n状态对应本机安装版本，不代表 GitHub 最新发行版。"
            else:
                # Serialize API-heavy queries to share the fresh cache and bound traffic.
                if self.command_lock.locked():
                    yield event.plain_result("已有 githook 查询正在处理，请稍后重试。")
                    return
                async with self.command_lock:
                    await self.refresh()
                    if text == "列表":
                        answer = "[githook 受管插件]\n" + (
                            "\n".join(
                                f"{p.display_name} · {p.version} · {p.status}\n  {p.name}"
                                for p in self.plugins
                            )
                            or "暂无。请检查 owners 和插件 metadata.yaml 的 repo；无仓库插件可配置 repo_overrides。"
                        )
                    elif text == "历史" or text.startswith("历史 "):
                        query = parse_history(text[2:].strip())
                        plugins = (
                            [resolve(self.plugins, query.plugin, self.settings.aliases)]
                            if query.plugin
                            else self.plugins
                        )
                        if not plugins:
                            answer = "暂无受管插件；请先用 /githook 列表 检查作者与仓库配置。"
                        elif self.github is None:
                            answer = "githook 尚未完成初始化，请稍后重试。"
                        else:
                            result = await self.github.history(
                                plugins, query, self.settings.history_branch
                            )
                            answer = format_history(
                                result,
                                query,
                                plugins[0].display_name if query.plugin else "全部受管插件",
                            )
                    else:
                        plugin = resolve(self.plugins, text, self.settings.aliases)
                        answer = f"[githook 插件信息]\n名称：{plugin.display_name}\n标识：{plugin.name}\n本机版本：{plugin.version}\n状态：{plugin.status}\n仓库：https://github.com/{plugin.repo}\n简介：{plugin.description or '未填写'}\n历史：/githook 历史 {plugin.name}"
            for part in chunks(answer):
                yield event.plain_result(part)
        except ValueError as exc:
            yield event.plain_result(str(exc))
        except Exception as exc:
            logger.error(f"githook 指令失败：{type(exc).__name__}")
            yield event.plain_result("githook 暂时无法完成操作，请稍后重试；管理员可检查插件日志。")

    async def terminate(self):
        if self.closed:
            return
        self.closed = True
        self.wake.set()
        self.scan_wake.set()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.session:
            await self.session.close()
        self.store.close()
