#!/usr/bin/env python3
"""Compute a management deadline from a calendar-year maximum or explicit date."""

import argparse
import datetime as dt
import json
import sys

YEARS = {"银行存款": 1, "动产": 2, "不动产": 3, "其他财产权": 3}


def calculate(data: dict) -> dict:
    category = data["category"]
    if category not in YEARS:
        raise ValueError("保全对象类型不在四类中，不能自动估算期限")
    ruling = dt.date.fromisoformat(data["ruling_date"])
    explicit_start = data.get("implementation_date")
    start = dt.date.fromisoformat(explicit_start) if explicit_start else ruling
    target_year = start.year + YEARS[category]
    try:
        maximum = start.replace(year=target_year)
    except ValueError:  # February 29 in a non-leap target year.
        maximum = start.replace(year=target_year, month=2, day=28)
    explicit_end = data.get("explicit_end_date")
    end = dt.date.fromisoformat(explicit_end) if explicit_end else maximum
    if end < start:
        raise ValueError("届满日早于起算日，请核对原文")
    if end > maximum:
        raise ValueError("文书届满日晚于所设分类上限，请人工核对，不能按上限自动登记")
    return {
        "category": category,
        "start_date": start.isoformat(),
        "start_basis": "文书明确实施日期" if explicit_start else "裁定日期暂代，待核实",
        "maximum_date": maximum.isoformat(),
        "estimated_end_date": end.isoformat(),
        "duration_days": (end - start).days,
        "unit": "天",
        "deadline_basis": "文书明确届满日期" if explicit_end else "分类上限暂算",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="JSON file; absent means stdin")
    args = parser.parse_args()
    try:
        with open(args.input, encoding="utf-8") if args.input else sys.stdin as stream:
            print(json.dumps(calculate(json.load(stream)), ensure_ascii=False))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"输入无法计算：{exc}\n")


if __name__ == "__main__":
    main()
