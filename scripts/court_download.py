#!/usr/bin/env python3
"""Fetch zxfw delivery PDFs, validate the batch, then publish without replacing files.

Only the documented zxfw API is automated here. Other delivery sites require
their own authenticated browser flow; no captcha or login bypass is attempted.
"""

import argparse
import datetime as dt
import io
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from download_validator import MAX_PDF_BYTES, validate_pdf_bytes
from sms_delivery import identify

API = "https://zxfw.court.gov.cn/yzw/yzw-zxfw-sdfw/api/v1/sdfw/getWsListBySdbhNew"
MAX_JSON_BYTES = 2 * 1024 * 1024
PARAMS = ("qdbh", "sdbh", "sdsin")


class DownloadError(Exception):
    pass


def safe_url(url, resolver=socket.getaddrinfo):
    if not isinstance(url, str):
        raise DownloadError("下载地址缺失")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise DownloadError("下载地址格式不正确") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DownloadError("下载地址不是可验证的 HTTPS 地址")
    if port not in (None, 443):
        raise DownloadError("下载地址使用非标准端口")
    try:
        addrs = resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addrs or any(not ipaddress.ip_address(item[4][0]).is_global for item in addrs):
            raise DownloadError("下载地址指向内网或非公共地址")
    except (socket.gaierror, ValueError, OSError) as exc:
        raise DownloadError("无法安全解析下载地址") from exc
    return url


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def delivery_params(link):
    parsed = urllib.parse.urlsplit(link)
    if parsed.scheme != "https" or parsed.hostname != "zxfw.court.gov.cn":
        raise DownloadError("仅支持 zxfw.court.gov.cn 的 HTTPS 送达链接；其他平台转浏览器处理")
    fragment = urllib.parse.urlsplit(parsed.fragment)
    merged = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    merged.update(urllib.parse.parse_qs(fragment.query, keep_blank_values=True))
    result = {}
    for key in PARAMS:
        values = merged.get(key, [])
        if len(values) != 1 or not 1 <= len(values[0]) <= 256:
            raise DownloadError("送达链接缺少或重复必要参数")
        result[key] = values[0]
    return result


def clean_name(value):
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", str(value or "文书"))
    return text.strip(" ._")[:80] or "文书"


def read_limited(response, limit):
    data = response.read(limit + 1)
    if len(data) > limit:
        raise DownloadError("平台响应超出大小限制")
    return data


def confirmed_suffix(date):
    if not date:
        return "收到时间待确认"
    try:
        return dt.date.fromisoformat(date).strftime("%Y%m%d收")
    except ValueError as exc:
        raise DownloadError("确认收到日期格式必须为 YYYY-MM-DD") from exc


def validated_pdf(opener, row, index):
    if not isinstance(row, dict):
        raise DownloadError("文书条目结构错误")
    url = safe_url(row.get("wjlj", ""))
    try:
        with opener.open(urllib.request.Request(url), timeout=60) as resp:
            if urllib.parse.urlsplit(resp.geturl()).scheme != "https":
                raise DownloadError("平台下载发生非 HTTPS 跳转")
            raw = read_limited(resp, MAX_PDF_BYTES)
            content_type = resp.headers.get("Content-Type", "")
    except OSError as exc:
        raise DownloadError("下载失败或链接已过期；请重新获取送达链接") from exc
    verdict = validate_pdf_bytes(raw, content_type=content_type)
    if not verdict["should_save_as_pdf"]:
        raise DownloadError(f"第 {index} 份文书拒存：{verdict['category']}")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted or len(reader.pages) == 0:
            raise ValueError("PDF 加密或没有页面")
    except Exception as exc:
        raise DownloadError(f"第 {index} 份响应不能打开成有效 PDF，拒存；核对 pypdf 依赖及原件") from exc
    return raw


def existing_matches(pending, target):
    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISREG(target_stat.st_mode) and target_stat.st_size == pending.stat().st_size:
        with pending.open("rb") as expected, target.open("rb") as actual:
            for block in iter(lambda: expected.read(65536), b""):
                if actual.read(len(block)) != block:
                    break
            else:
                if not actual.read(1):
                    return True
    raise DownloadError("目标文书已存在且内容不同或类型不符；拒绝覆盖，请查重")


def publish(staged):
    # Resolve known conflicts before any new PDF is made visible.
    pending_files = [(pending, target) for pending, target in staged if not existing_matches(pending, target)]
    for pending, target in pending_files:
        try:
            os.link(pending, target)
        except FileExistsError:
            if not existing_matches(pending, target):
                raise DownloadError("发布期间目标文书发生变化；请重试") from None


def fetch(link, directory, received_date=None, opener=None):
    params = delivery_params(link)
    suffix = confirmed_suffix(received_date)
    opener = opener or urllib.request.build_opener(CheckedRedirect())
    request = urllib.request.Request(API, json.dumps(params).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=30) as resp:
            payload = json.loads(read_limited(resp, MAX_JSON_BYTES))
    except (OSError, ValueError) as exc:
        raise DownloadError("送达平台未返回有效文书列表；可转人工浏览器") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    # The live zxfw response has used HTTP-style application code 200, while
    # older examples used 0. Both still require a nonempty document list.
    if isinstance(payload, dict) and payload.get("code", 0) not in (0, "0", 200, "200"):
        raise DownloadError("送达平台拒绝本次查询；不继续下载")
    if not isinstance(rows, list) or not rows:
        raise DownloadError("送达平台响应结构与已知接口不符；停止下载")
    if len(rows) > 50:
        raise DownloadError("文书条目过多，停止自动下载并请求人工核对")
    results = []
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".validated-", dir=directory) as staging:
            staged = []
            for index, row in enumerate(rows, 1):
                pending = Path(staging) / str(index)
                pending.touch(mode=0o600)
                pending.write_bytes(validated_pdf(opener, row, index))
                title = clean_name(row.get("c_wsmc"))
                target = directory / f"{title}_{index}_{suffix}.pdf"
                staged.append((pending, target))
                results.append({"file": str(target), "title": title,
                                "platform_record_time": row.get("dt_cjsj"),
                                "received_date_confirmed": bool(received_date)})
            publish(staged)
    except OSError as exc:
        raise DownloadError("无法在指定目录安全暂存或保存文书；请检查权限、空间及硬链接支持") from exc
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--link-stdin", action="store_true",
                        help="从标准输入读原始送达链接")
    source.add_argument("--sms-stdin", action="store_true",
                        help="从标准输入读短信正文，仅下载唯一识别的统一送达链接")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--received-date", help="仅填经当事人确认的实际收到日 YYYY-MM-DD")
    args = parser.parse_args()
    try:
        text = sys.stdin.read().strip()
        link = text
        if args.sms_stdin:
            matches = identify(text)
            if len(matches) != 1:
                raise DownloadError("短信须包含唯一可识别的法院送达链接")
            link, (platform, strategy) = next(iter(matches.items()))
            if platform != "zxfw.court.gov.cn" or strategy != "api_first":
                raise DownloadError("该平台不支持本脚本下载；按已授权的浏览器流程处理")
        result = fetch(link, args.output_dir, args.received_date)
        print(json.dumps({"downloaded": result}, ensure_ascii=False))
    except DownloadError as exc:
        parser.exit(2, f"下载已停止：{exc}\n")


if __name__ == "__main__":
    main()
