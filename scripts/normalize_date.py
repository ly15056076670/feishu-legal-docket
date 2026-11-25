#!/usr/bin/env python3
"""Convert numeric or Chinese legal-document dates to YYYY-MM-DD."""

import argparse
import datetime as dt
import re

YEAR = str.maketrans("零〇○ＯO一二三四五六七八九", "00000123456789")
DIGIT = {"零": 0, "〇": 0, "○": 0, "Ｏ": 0, "O": 0, "一": 1, "二": 2, "三": 3,
         "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
DIGIT.update({str(digit): digit for digit in range(10)})


def number(value: str) -> int:
    value = value.strip()
    if value.isdecimal():
        return int(value)
    if "十" in value:
        head, tail = value.split("十", 1)
        if (head and head not in DIGIT) or (tail and tail not in DIGIT):
            raise ValueError("日期数字格式不明确")
        return (DIGIT[head] if head else 1) * 10 + (DIGIT[tail] if tail else 0)
    if not value or any(c not in DIGIT for c in value):
        raise ValueError("日期数字格式不明确")
    return int("".join(str(DIGIT[c]) for c in value))


def normalize(value: str) -> str:
    text = re.sub(r"\s+", "", value)
    m = re.fullmatch(r"([0-9零〇○ＯO一二三四五六七八九]{4})年([0-9零〇○ＯO一二三四五六七八九十]{1,3})月([0-9零〇○ＯO一二三四五六七八九十]{1,3})日?", text)
    if m:
        y = m.group(1).translate(YEAR)
        if not y.isdecimal():
            raise ValueError("年份不明确")
        return dt.date(int(y), number(m.group(2)), number(m.group(3))).isoformat()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        return dt.date(*map(int, m.groups())).isoformat()
    raise ValueError("缺少完整年月日或日期格式不明确")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="e.g. 二○二六年一月二十二日")
    args = parser.parse_args()
    try:
        print(normalize(args.date))
    except ValueError:
        parser.exit(2, "日期不完整或无效，请核对文书原件。\n")
