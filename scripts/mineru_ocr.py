#!/usr/bin/env python3
"""OCR one local document with MinerU v4; retrieve token from env or macOS Keychain."""

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

API = "https://mineru.net/api/v4"
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".doc", ".docx"}
MAX_SOURCE = 200 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
TIMEOUT_SECONDS = 240
KEYCHAIN_SERVICE = "feishu-legal-docket-mineru"
KEYCHAIN_ACCOUNT = "api-token"


class ParseError(Exception):
    """Safe error message that never contains credentials or signed URLs."""


def get_token() -> str:
    token = os.environ.get("MINERU_API_TOKEN", "").strip()
    if token:
        return token
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                 "-a", KEYCHAIN_ACCOUNT, "-w"],
                capture_output=True, text=True, timeout=60, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    raise ParseError(
        "没有可用的 MinerU 凭据；请把新的 Token 私密保存到 macOS 钥匙串项目 "
        f"{KEYCHAIN_SERVICE}（账户 {KEYCHAIN_ACCOUNT}），或设置 MINERU_API_TOKEN。"
    )


def json_request(opener, method, url, token, payload=None):
    try:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Authorization": f"Bearer {token}", **({"Content-Type": "application/json"} if payload is not None else {})},
            method=method,
        )
        with opener.open(request, timeout=40) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ParseError("MinerU 请求失败或响应不是 JSON；检查网络、Token、额度及服务状态") from exc
    if not isinstance(result, dict) or result.get("code") != 0:
        raise ParseError("MinerU 返回非成功状态；检查 Token、文件类型或额度")
    data = result.get("data")
    if not isinstance(data, dict):
        raise ParseError("MinerU 返回缺少任务数据")
    return data


def read_markdown(archive: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            candidates = [entry for entry in bundle.infolist() if not entry.is_dir() and Path(entry.filename).name == "full.md"]
            if len(candidates) != 1 or candidates[0].file_size > MAX_ARCHIVE:
                raise ParseError("解析包没有唯一的 full.md，或文本过大")
            content = bundle.read(candidates[0]).decode("utf-8")
            if not content.strip():
                raise ParseError("解析得到空白文本；请检查原件")
            return content
    except (zipfile.BadZipFile, UnicodeError, RuntimeError) as exc:
        raise ParseError("MinerU 结果压缩包无法读取") from exc


def parse_local(path: Path, token: str, opener=None, timeout_seconds: int = TIMEOUT_SECONDS) -> str:
    if not path.is_file() or path.suffix.lower() not in ALLOWED:
        raise ParseError("请提供可读取的 PDF、图片或 Word 文件")
    if path.stat().st_size == 0 or path.stat().st_size > MAX_SOURCE:
        raise ParseError("文件为空或超过 MinerU 的 200MB 上限")
    opener = opener or urllib.request.build_opener()
    data = json_request(
        opener, "POST", f"{API}/file-urls/batch", token,
        {"files": [{"name": path.name, "is_ocr": True}], "model_version": "vlm", "language": "ch"},
    )
    batch_id, upload_urls = data.get("batch_id"), data.get("file_urls")
    if not batch_id or not isinstance(upload_urls, list) or len(upload_urls) != 1:
        raise ParseError("MinerU 未返回有效的上传任务")
    # Presigned upload must not receive the Bearer token or an added Content-Type.
    try:
        with path.open("rb") as source:
            upload_request = urllib.request.Request(upload_urls[0], data=source.read(), method="PUT")
        with opener.open(upload_request, timeout=120):
            pass
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ParseError("原件上传 MinerU 失败；未创建任何飞书记录") from exc
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = json_request(opener, "GET", f"{API}/extract-results/batch/{batch_id}", token)
        entries = result.get("extract_result", [])
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise ParseError("MinerU 批量结果未返回唯一文件")
        state = entries[0].get("state")
        if state == "failed":
            raise ParseError("MinerU OCR 解析失败；请核对原件与额度")
        if state == "done":
            zip_url = entries[0].get("full_zip_url")
            if not isinstance(zip_url, str) or not zip_url.startswith("https://"):
                raise ParseError("MinerU 未返回 HTTPS 结果包地址")
            try:
                with opener.open(urllib.request.Request(zip_url), timeout=60) as response:
                    archive = response.read(MAX_ARCHIVE + 1)
                if len(archive) > MAX_ARCHIVE:
                    raise ParseError("MinerU 解析结果包过大")
                return read_markdown(archive)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ParseError("MinerU 结果下载失败") from exc
        if state not in {"waiting-file", "pending", "running", "converting"}:
            raise ParseError("MinerU 返回未知任务状态")
        time.sleep(min(4, max(0, deadline - time.monotonic())))
    raise ParseError("MinerU OCR 等待超时；未写入飞书，可用 batch_id 在平台查任务")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--output", type=Path, help="Optional path for OCR Markdown; otherwise print Markdown")
    parser.add_argument("--check-token", action="store_true", help="Only check if a private credential is available; do not print it")
    args = parser.parse_args()
    try:
        token = get_token()
        if args.check_token:
            print("MinerU 凭据已就绪；未发起网络请求。")
            return
        if args.file is None:
            parser.error("请提供要识别的文件，或使用 --check-token")
        content = parse_local(args.file, token)
        if args.output:
            args.output.write_text(content, encoding="utf-8")
            print("MinerU OCR 完成，文本已保存；请结合原件逐页核对。", file=sys.stderr)
        else:
            print(content)
    except ParseError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
