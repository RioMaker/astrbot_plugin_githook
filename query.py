"""History syntax and plain-text presentation. Dates use UTC+08:00."""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def clean_title(value):
    lines = str(value or "").splitlines()
    return (
        "".join(c for c in (lines[0] if lines else "（无标题）") if c.isprintable()) or "（无标题）"
    )


@dataclass(frozen=True)
class HistoryQuery:
    plugin: str = ""
    start: int = 1
    end: int = 30
    since: str = ""
    until: str = ""


def date_bound(value, end=False):
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD") from exc
    if not 1970 <= d.year <= 2099:
        raise ValueError("日期年份必须在 1970–2099 之间")
    dt = datetime.combine(d, time(23, 59, 59) if end else time.min, BEIJING)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_history(text):
    tokens = text.split()
    plugin, start, end, since, until = "", 1, 30, "", ""
    selected = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"页", "page"}:
            i += 1
            if selected or i == len(tokens) or not tokens[i].isdigit():
                raise ValueError("页码用法：/githook 历史 六爻 页 2")
            page = int(tokens[i])
            start, end, selected = (page - 1) * 30 + 1, page * 30, True
        elif token.isdigit() or re.fullmatch(r"第\d+页", token):
            if selected:
                raise ValueError("只能指定一个页码或条目范围")
            page = int(token if token.isdigit() else token[1:-1])
            start, end, selected = (page - 1) * 30 + 1, page * 30, True
        elif match := re.fullmatch(r"(\d+)[-~～](\d+)", token):
            if selected:
                raise ValueError("只能指定一个页码或条目范围")
            start, end, selected = int(match[1]), int(match[2]), True
        elif ".." in token or re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            if since:
                raise ValueError("只能指定一个日期范围")
            if ".." in token:
                dates = token.split("..")
                if len(dates) != 2:
                    raise ValueError("日期范围用法：2026-09-01..2026-09-24")
            else:
                dates = [token, token]
                if i + 1 < len(tokens) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", tokens[i + 1]):
                    i += 1
                    dates[1] = tokens[i]
            since, until = date_bound(dates[0]), date_bound(dates[1], True)
            if since > until:
                raise ValueError("开始日期不能晚于结束日期")
        elif not plugin:
            plugin = token
        else:
            raise ValueError(f"无法识别参数“{token}”；用 /githook 帮助 查看范围写法")
        i += 1
    if start < 1 or end < start or end - start + 1 > 100 or end > 3000:
        raise ValueError("条目范围必须为 1–3000，单次最多 100 条；更早记录请加日期范围")
    return HistoryQuery(plugin, start, end, since, until)


def chunks(text, limit=1600):
    """Split long query results without dropping requested titles."""
    result, buf = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if buf:
                result.append(buf.rstrip())
                buf = ""
            result.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) > limit:
            result.append(buf.rstrip())
            buf = ""
        buf += line
    if buf:
        result.append(buf.rstrip())
    return result
