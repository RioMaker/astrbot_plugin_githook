import logging
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def module(name):
    result = types.ModuleType(name)
    result.__path__ = []
    sys.modules[name] = result
    return result


def decorate(*args, **kwargs):
    return lambda obj: obj


for name in (
    "astrbot",
    "astrbot.api",
    "astrbot.core",
    "astrbot.core.star",
    "astrbot.core.star.filter",
    "astrbot.core.utils",
):
    module(name)
api = sys.modules["astrbot.api"]
api.AstrBotConfig = dict
api.logger = logging.getLogger("githook-test")
event_module = module("astrbot.api.event")
event_module.AstrMessageEvent = object
event_module.filter = types.SimpleNamespace(
    command=decorate, on_plugin_loaded=decorate, on_plugin_unloaded=decorate
)


class MessageChain:
    def __init__(self, chain):
        self.chain = chain


event_module.MessageChain = MessageChain
components = module("astrbot.api.message_components")


class Plain:
    def __init__(self, text):
        self.text = text


components.Plain = Plain
star_module = module("astrbot.api.star")


class Star:
    def __init__(self, context):
        self.context = context


star_module.Star = Star
star_module.Context = object
star_module.register = decorate
module("astrbot.core.star.filter.command").GreedyStr = type("GreedyStr", (str,), {})
paths = module("astrbot.core.utils.astrbot_path")
paths.get_astrbot_data_path = lambda: "unused"
paths.get_astrbot_plugin_path = lambda: "unused"


@pytest.fixture
def store(tmp_path):
    from astrbot_plugin_githook.store import Store

    result = Store(tmp_path / "data" / "test.db")
    yield result
    result.close()
