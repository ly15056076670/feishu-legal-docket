#!/usr/bin/env python3
"""Recognize court delivery links without printing URL parameters."""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


URL = re.compile(r"""https?://[^\s<>"'，。；、？！《》【】「」“”（）]+""", re.IGNORECASE)
PLATFORMS = Path(__file__).resolve().parents[1] / "references/sms-patterns.json"


def identify(text):
    platforms = json.loads(PLATFORMS.read_text(encoding="utf-8"))["download_link_patterns"]
    identified = {}
    for candidate in URL.findall(text):
        link = candidate.rstrip(".,;:!?)")
        try:
            parsed = urlsplit(link)
            if parsed.username or parsed.password or not parsed.hostname:
                continue
        except ValueError:
            continue
        for platform in platforms:
            allowed_hosts = {platform["domain"], *platform.get("alias_domains", [])}
            if parsed.netloc.lower() not in allowed_hosts:
                continue
            if not re.match(platform["pattern"], link, flags=re.IGNORECASE):
                continue
            strategy = platform["download_strategy"] if parsed.scheme == "https" else "manual_verification"
            identified[link] = (platform["domain"], strategy)
            break
    return identified


if __name__ == "__main__":
    matches = identify(sys.stdin.read())
    print(json.dumps({
        "identified": [
            {"platform": platform, "strategy": strategy}
            for platform, strategy in matches.values()
        ],
    }, ensure_ascii=False))
