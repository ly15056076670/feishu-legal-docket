#!/usr/bin/env python3
"""Read each PDF page's existing text layer; empty/illegible pages require MinerU."""

import argparse
import json
from pathlib import Path


def read_pages(path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf；请安装 scripts/requirements.txt") from exc
    reader = PdfReader(str(path), strict=False)
    return [{"page": index, "text": page.extract_text() or ""}
            for index, page in enumerate(reader.pages, 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    if not args.file.is_file() or args.file.suffix.lower() != ".pdf":
        parser.error("请提供存在的 PDF 文件")
    try:
        pages = read_pages(args.file)
    except Exception as exc:
        parser.exit(2, f"PDF 文字层读取失败：{exc}\n")
    print(json.dumps({"pages": pages, "page_count": len(pages)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
