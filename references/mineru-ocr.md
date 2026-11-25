# 扫描件必经 MinerU OCR

权威资料：[MinerU 文档解析接口文档](https://mineru.net/apiManage/docs)（官方）。文字层无法可靠提取的扫描件、乱码页按用户比赛要求**先调用 MinerU**；结果用于提取前逐页核对，原件另行作为来源附件。缺少安全凭据时不得用视觉直读绕过此步骤，也不得猜测正文入表。

## 本地文件路径

使用 `scripts/mineru_ocr.py /path/to/file.pdf --output /path/to/ocr.md`。脚本从 `MINERU_API_TOKEN` 或 macOS 钥匙串读取授权，不接受命令行 Token。先用 `python3 scripts/mineru_ocr.py --check-token` 只核对凭据存在，不发请求、不回显 Token：

1. POST `https://mineru.net/api/v4/file-urls/batch`；Bearer Token；JSON `files: [{name: 文件名, is_ocr: true}]`、`model_version: "vlm"`、`language: "ch"`。MinerU 官方规定本地上传接口返回 `batch_id` 与临时 `file_urls`；单次最多 50 件，文件不超过 200MB/200 页，临时 URL 有效 24 小时。本脚本每次仅提交一个用户文件。
2. 对返回的临时 URL 用 PUT 上传**文件原始字节**，不要给临时 URL 发送 Bearer Token，也不要额外设置 Content-Type。成功上传后平台自动发起解析，无需另提交。
3. GET `https://mineru.net/api/v4/extract-results/batch/{batch_id}` 轮询，直到该文件 `state=done`；`failed` 立即停止；超时有界停止，避免重复上传。完成结果的 `full_zip_url` 是结果压缩包，提取 `full.md`；不得打印带签名的 URL 或整个响应。
4. 将 Markdown 与原件页图对应核对姓名、案号、日期、金额和处分语句。标题或 OCR 字样不足以证明具体措施。只把原件作为飞书附件，不把 OCR 临时下载地址充作来源文书。

可公开访问的文件 URL 也可走官方单文件 `POST /api/v4/extract/task` → `GET /api/v4/extract/task/{task_id}`，`is_ocr=true`；若来源是私有飞书链接，应先安全下载成当前任务本地文件，**不要把私有分享链接误当公共 URL**。脚本实现的是本地文件上传流程。

## 设置与限制

MinerU 需要在**执行脚本的同一环境**提供 `MINERU_API_TOKEN`，或由脚本可访问的 macOS 钥匙串项目 `feishu-legal-docket-mineru`（账户 `api-token`）提供；其他进程或设备的钥匙串项目不自动可用。不得把密钥发到聊天、写入技能包或回显。没有安全凭据、无法执行脚本或访问 MinerU 时，停止受影响扫描文书的后续写入并报告；文字层完整的其他文书可继续处理。飞书连接不会自动提供 MinerU 授权。

仅对用户这次指定的法律文件调用；不把旁边的工作目录整体上传。HTTP/OCR 错误不应触发任何飞书写入。结果中每页与原件比对，超时或转换失败只报告原因与下一步，不擅自换 OCR 服务或改用臆测正文。
