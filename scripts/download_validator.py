#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
court-doc-extract 下载文件校验器
================================
法院文书下载后、落地到 <download_dir>/<案号>/ 之前的**唯一校验 Gate**。

职责
----
1. 内容类型比对：声明 Content-Type 与文件 magic bytes 是否一致。
2. 文件大小阈值：过小（截断/空）、过大（异常）报警。
3. PDF 文件头与可读性：必须以 `%PDF` 开头、尾部尽量含 `%%EOF`，
   否则视为损坏 / 不完整。
4. 伪装识别：登录页、错误页、验证码/风控页即使后缀或 Content-Type
   声称是 PDF，也一律识别为「非 PDF」，禁止保存为 PDF。

设计原则（隐私安全）
--------------------
- 纯标准库，无第三方依赖，任意 python3 可直接 `python3 download_validator.py <path>`。
- 不读取任何案件正文、不输出完整文书内容；只输出分类与原因。
- 完全可脱敏测试：所有判定基于传入的字节，不依赖真实网络。

能力类型（与 SKILL.md / sms-download-guide.md 对应）
--------------------------------------------------
- API 直连：后端公开接口返回签名下载链接，curl 拉取（校验后仍须过本 Gate）。
- 浏览器处理：agent-browser 打开页面交互下载。
- 需人工接管：验证码 / 登录失败 / 风控页出现时停止自动化。
- 暂不支持：无公开接口且无浏览器可用平台，提示用户手动下载。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ---- 内容分类常量 ----
CONTENT_PDF = "pdf"
CONTENT_LOGIN_PAGE = "login_page"
CONTENT_ERROR_PAGE = "error_page"
CONTENT_CAPTCHA_PAGE = "captcha_page"
CONTENT_HTML_OTHER = "html_other"
CONTENT_EMPTY = "empty"
CONTENT_UNKNOWN = "unknown"

# ---- 阈值 ----
# 小于此字节数视为异常（空 / 截断），合法 PDF 通常远大于此。
MIN_PDF_BYTES = 200
# 大于此字节数视为异常（可能是大二进制被误当作文书），仅告警不拒绝。
MAX_PDF_BYTES = 200 * 1024 * 1024  # 200 MiB

PDF_MAGIC = b"%PDF"
PDF_EOF = b"%%EOF"

# 在正文（小写）中命中即判定为「验证码 / 风控页」。
CAPTCHA_MARKERS = [
    "captcha", "验证码", "图形验证码", "滑块", "slide", "拼图",
    "风控", "risk", "行为验证", "安全验证", "人机验证", "点击验证",
    "请完成", "拖动", "rotate", "rotatecaptcha",
]
# 命中即判定为「登录页」。
LOGIN_MARKERS = [
    "请登录", "登录", "login", "sign in", "signin", "用户登录",
    "账号密码", "请输入密码", "请输入您的密码", "用户名", "手机号登录",
    "账户登录", "登录系统", "统一登录",
]
# 命中即判定为「错误页 / 异常页」。
ERROR_MARKERS = [
    "error", "exception", "404", "500", "页面不存在", "访问失败",
    "系统繁忙", "请求失败", "服务器错误", "not found", "forbidden",
    "禁止访问", "无权限", "会话过期", "session expired", "token 失效",
]


def _norm_text(raw: bytes) -> str:
    return raw.decode("utf-8", "ignore").lower()


def classify_bytes(raw: bytes) -> str:
    """根据 magic bytes + 内容嗅探，判定内容分类。不依赖扩展名。"""
    if raw is None or len(raw) == 0:
        return CONTENT_EMPTY

    head = raw[:4096]

    # 1) PDF：以 %PDF 开头（允许前导空白）即视为 PDF 候选。
    stripped_head = head.lstrip()
    if stripped_head.startswith(PDF_MAGIC):
        return CONTENT_PDF

    # 2) HTML / 标记语言：识别标签结构后按语义细分。
    text = _norm_text(raw)
    if (b"<html" in head or b"<!doctype" in head
            or b"<head" in head or b"<body" in head or b"<?xml" in head):
        if any(m in text for m in CAPTCHA_MARKERS):
            return CONTENT_CAPTCHA_PAGE
        if any(m in text for m in LOGIN_MARKERS):
            return CONTENT_LOGIN_PAGE
        if any(m in text for m in ERROR_MARKERS):
            return CONTENT_ERROR_PAGE
        return CONTENT_HTML_OTHER

    # 3) 非 PDF 也非可识别 HTML。
    return CONTENT_UNKNOWN


def validate_pdf_bytes(raw: bytes, content_type: str | None = None,
                       min_size: int = MIN_PDF_BYTES,
                       max_size: int = MAX_PDF_BYTES) -> dict:
    """对下载字节做落地前校验，返回结构化结果（不抛异常）。

    返回字段
    --------
    category            : classify_bytes 的分类
    is_pdf              : 是否可作为 PDF 保存
    should_save_as_pdf  : 同 is_pdf 的语义别名
    reasons             : 人类可读原因列表（可能含告警与拒绝理由）
    content_type        : 传入的声明 Content-Type（可能 None）
    size_bytes          : 字节长度
    """
    reasons: list[str] = []
    category = classify_bytes(raw)
    size = len(raw) if raw is not None else 0

    # 空文件
    if category == CONTENT_EMPTY:
        reasons.append("文件为空（0 字节），不是 PDF，禁止保存为 PDF。")
        return {
            "category": category,
            "is_pdf": False,
            "should_save_as_pdf": False,
            "reasons": reasons,
            "content_type": content_type,
            "size_bytes": size,
        }

    # Content-Type 与内容不一致 → 伪装告警（但不单独据此拒绝，
    # 真正的拒绝由分类决定；此告警帮助定位伪装来源）。
    ct_pdf = bool(content_type) and "pdf" in (content_type or "").lower()
    if ct_pdf and category != CONTENT_PDF:
        reasons.append(
            f"Content-Type 声明为 PDF（{content_type}），但内容被识别为"
            f"「{category}」，疑似伪装，禁止保存为 PDF。")

    # 非 PDF（登录页 / 错误页 / 验证码页 / 未知）
    if category != CONTENT_PDF:
        reasons.append(
            f"内容被识别为「{category}」，不是 PDF，禁止保存为 PDF。")
        return {
            "category": category,
            "is_pdf": False,
            "should_save_as_pdf": False,
            "reasons": reasons,
            "content_type": content_type,
            "size_bytes": size,
        }

    # 以下为 PDF 的健壮性校验
    head = raw[:4096]
    if size < min_size:
        reasons.append(
            f"PDF 体积过小（{size} < {min_size} 字节），疑似截断或破损，"
            f"禁止保存为 PDF。")
        return {
            "category": category,
            "is_pdf": False,
            "should_save_as_pdf": False,
            "reasons": reasons,
            "content_type": content_type,
            "size_bytes": size,
        }

    if size > max_size:
        reasons.append(
            f"PDF 体积异常偏大（{size} > {max_size} 字节），已告警但不拒绝；"
            f"请人工确认是否为正常文书。")

    # 文件头
    if not head.lstrip().startswith(PDF_MAGIC):
        reasons.append("文件头不是 %PDF，不是合法 PDF，禁止保存为 PDF。")
        return {
            "category": category,
            "is_pdf": False,
            "should_save_as_pdf": False,
            "reasons": reasons,
            "content_type": content_type,
            "size_bytes": size,
        }

    # 可读性：尾部尽量含 %%EOF（部分流式 PDF 可能缺失 → 仅告警）
    if PDF_EOF not in raw[-2048:]:
        reasons.append(
            "未在尾部发现 %%EOF 结束标记，PDF 可能不完整/不可读，请人工确认。")

    reasons.append("PDF 头部与体积校验通过，可保存为 PDF。")
    return {
        "category": category,
        "is_pdf": True,
        "should_save_as_pdf": True,
        "reasons": reasons,
        "content_type": content_type,
        "size_bytes": size,
    }


def validate_downloaded_file(path: str, content_type: str | None = None,
                             min_size: int = MIN_PDF_BYTES,
                             max_size: int = MAX_PDF_BYTES) -> dict:
    """对磁盘文件做校验；文件缺失时返回明确的拒绝结果。"""
    if not os.path.exists(path):
        return {
            "category": CONTENT_UNKNOWN,
            "is_pdf": False,
            "should_save_as_pdf": False,
            "reasons": [f"文件不存在：{path}"],
            "content_type": content_type,
            "size_bytes": 0,
        }
    with open(path, "rb") as f:
        raw = f.read()
    return validate_pdf_bytes(raw, content_type, min_size, max_size)


def is_captcha_or_risk_page(raw: bytes) -> bool:
    """便捷判定：下载内容是否为验证码 / 风控页（出现时须停止自动化）。"""
    return classify_bytes(raw) == CONTENT_CAPTCHA_PAGE


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="下载文件校验器：识别 PDF / 登录页 / 错误页 / 验证码页 / 空文件，"
                    "禁止把伪装 PDF 保存为文书。")
    ap.add_argument("path", help="待校验文件路径")
    ap.add_argument("--content-type", default=None,
                    help="下载响应的 Content-Type（如 application/pdf），用于比对伪装")
    ap.add_argument("--min-size", type=int, default=MIN_PDF_BYTES,
                    help=f"最小字节数阈值（默认 {MIN_PDF_BYTES}）")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出完整结果（默认已为 JSON 行）")
    args = ap.parse_args(argv)

    result = validate_downloaded_file(
        args.path, content_type=args.content_type, min_size=args.min_size)
    print(json.dumps(result, ensure_ascii=False))
    # 可作为 PDF 保存 → 退出码 0；否则 1（供脚本/CI 判定）。
    return 0 if result["should_save_as_pdf"] else 1


if __name__ == "__main__":
    sys.exit(main())
