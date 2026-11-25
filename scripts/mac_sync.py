#!/usr/bin/env python3
"""Sync confirmed hearing/deadline items with the bundled macOS GUI applets.

Input JSON: {"items":[{"kind":"hearing|deadline|preservation", "case_no":"...",
"node_key":"stable key", "title":"...", "at":"YYYY-MM-DD HH:MM", "confirmed":true,
"location":"...", "source":"...", "renewed":false}]}
Date-only deadlines can use YYYY-MM-DD, with 09:00 as a marked local reminder time.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT = Path.home() / "Library/Application Support/CourtDocExtract"
SYNC = ROOT / "sync"
MARKER = "court-doc-extract-managed"
BEIJING_TIME = dt.timezone(dt.timedelta(hours=8))


def safe_field(value):
    return str(value or "").replace("|||", "｜").replace("\n", " ").replace("\r", " ").strip()


def task_id(item, lead="base"):
    raw = f"{item['case_no']}|{item['kind']}|{item['node_key']}|{lead}"
    return "cde-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def parsed_time(value):
    if len(value) == 10:
        stamp = dt.datetime.strptime(value, "%Y-%m-%d").replace(hour=9)
        return stamp, True
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M"), False


def prepare(items, as_of=None):
    now = as_of or dt.datetime.now(BEIJING_TIME).replace(tzinfo=None)
    planned = {}
    blocked = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            blocked.append({"index": index, "reason": "事项须为对象"})
            continue
        kind = item.get("kind")
        if kind not in ("hearing", "deadline", "preservation"):
            blocked.append({"index": index, "reason": "未知节点类型"})
            continue
        if item.get("confirmed") is not True:
            blocked.append({"index": index, "reason": "节点或变更尚未确认"})
            continue
        if not all(safe_field(item.get(k)) for k in ("case_no", "node_key")):
            blocked.append({"index": index, "reason": "缺案号或稳定节点键"})
            continue
        renewed = item.get("renewed", False)
        status = item.get("status", "effective")
        if kind == "preservation" and not isinstance(renewed, bool):
            blocked.append({"index": index, "reason": "续保状态必须为布尔值"})
            continue
        node_plan = planned.setdefault(task_id(item), [])
        if kind == "preservation" and renewed:
            reminder = []
            for lead in (60, 30, 0):
                tid = task_id(item, f"before-{lead}")
                body = f"{MARKER} task_id={tid}"
                reminder.append("|||".join(("续保已办理", "CANCEL", "♾️重要期限提醒", body)))
            node_plan.append((index, reminder, []))
            continue
        if kind == "hearing" and status == "cancelled":
            tid = task_id(item, "before-0")
            body = f"{MARKER} task_id={tid}"
            reminder = ["|||".join(("开庭已取消", "CANCEL", "开庭", body))]
            calendar = ["|||".join(("开庭已取消", "CANCEL", "", "开庭", body))]
            node_plan.append((index, reminder, calendar))
            continue
        if status != "effective":
            blocked.append({"index": index, "reason": "节点已失效或状态不明确"})
            continue
        if not all(safe_field(item.get(k)) for k in ("title", "at", "source")):
            blocked.append({"index": index, "reason": "缺标题、时间或来源"})
            continue
        try:
            stamp, date_only = parsed_time(item["at"])
        except (ValueError, TypeError):
            blocked.append({"index": index, "reason": "日期格式错误"})
            continue
        if kind == "hearing" and date_only:
            blocked.append({"index": index, "reason": "开庭缺具体时刻"})
            continue
        if stamp < now:
            blocked.append({"index": index, "reason": "过去的时刻不创建新提醒"})
            continue
        leads = (60, 30, 0) if kind == "preservation" else (0,)
        reminder, calendar = [], []
        for lead in leads:
            when = stamp - dt.timedelta(days=lead)
            if when < now:
                continue
            tid = task_id(item, f"before-{lead}")
            title = safe_field(item["title"])
            if lead:
                title = f"{title}｜到期前{lead}天"
            body = f"{MARKER} task_id={tid} 来源={safe_field(item['source'])}"
            if date_only:
                body += " 日期未载明时刻，提醒设在09:00"
            when_text = when.strftime("%Y-%m-%d %H:%M")
            list_name = "开庭" if kind == "hearing" else "♾️重要期限提醒"
            reminder.append("|||".join((title, when_text, list_name, body)))
            if kind == "hearing":
                location = safe_field(item.get("location"))
                calendar.append("|||".join((title, when_text, location, "开庭", body)))
        node_plan.append((index, reminder, calendar))

    reminder, calendar = [], []
    for alternatives in planned.values():
        if not alternatives:
            continue
        _, proposed_reminder, proposed_calendar = alternatives[0]
        if any((rem, cal) != (proposed_reminder, proposed_calendar) for _, rem, cal in alternatives[1:]):
            blocked.extend({"index": index, "reason": "同一节点的确认状态或内容存在冲突"}
                           for index, _, _ in alternatives)
            continue
        reminder.extend(proposed_reminder)
        calendar.extend(proposed_calendar)
    return {"reminder": reminder, "calendar": calendar, "blocked": blocked}


def atomic_write(path, value):
    fd, tmp = tempfile.mkstemp(prefix=".task-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(value)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def invoke(kind, rows):
    app = ROOT / f"wb-{kind}.app"
    if not app.is_dir():
        return {"failed": len(rows), "reason": "缺少已编译的 Mac applet；先运行 scripts/macos/setup_apps.sh"}
    run_id = str(uuid.uuid4())
    task = SYNC / f"{kind}_task.txt"
    result = SYNC / f"{kind}_result.txt"
    result.unlink(missing_ok=True)
    atomic_write(task, f"RUN_ID={run_id}\n" + "\n".join(rows) + "\n")
    try:
        subprocess.run(["open", "-W", str(app)], check=True, timeout=120, capture_output=True)
        payload = result.read_text(encoding="utf-8")
        lines = dict(line.split("=", 1) for line in payload.splitlines() if "=" in line)
        if lines.get("RUN_ID") != run_id:
            raise ValueError("回执不属于本次执行")
        counts = {key: int(lines[key]) for key in ("created", "updated", "skipped", "failed")}
        if sum(counts.values()) != len(rows):
            raise ValueError("回执数量与提交数量不符")
        return counts
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, UnicodeError) as exc:
        return {"failed": len(rows), "reason": f"运行或回执校验失败：{type(exc).__name__}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        parser.error("输入文件需要 items 数组")
    prepared = prepare(data["items"])
    report = {"planned": {kind: len(prepared[kind]) for kind in ("reminder", "calendar")},
              "blocked": prepared["blocked"], "dry_run": args.dry_run}
    if not args.dry_run:
        if platform.system() != "Darwin":
            parser.error("实际同步仅支持 macOS；其他系统可使用 --dry-run")
        if dt.datetime.now().astimezone().utcoffset() != dt.timedelta(hours=8):
            parser.error("Mac 系统时区须先核对为北京时间 UTC+8，避免开庭时间错位")
        import fcntl

        SYNC.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(SYNC, 0o700)
        with (SYNC / ".sync.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            report["actual"] = {kind: invoke(kind, prepared[kind]) for kind in ("reminder", "calendar")
                                if prepared[kind]}
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
