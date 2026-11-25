# 诉讼节点台账助手

从法院送达短信或文书中识别开庭、改期、保全和其他明确期限，核对原件后增量登记到飞书，并按授权同步日历与提醒。

## 在 Agent 中使用

1. 将整个仓库放在支持 Agent Skills 的工作区中，指定 [SKILL.md](SKILL.md)。Codex 用户也可将目录放进 `~/.agents/skills/` 后调用 `$feishu-legal-docket`。
2. 给当前 Agent 一份传票、保全裁定、开庭改期通知，或包含原始送达链接的法院短信。可先只看提取结果和更新计划。
3. 需要飞书写入时，按[平台接入](references/agent-setup.md)连接宿主飞书工具或官方 Lark CLI，指定已授权的目标 Base、表和可选飞书日历。先用[合成试验](examples/isolated-trial.md)走新增、重复、改期和定向清理。

## 功能与运行条件

| 功能 | 实现与运行条件 |
| --- | --- |
| 法院短信链接 | 输入用户粘贴的短信或提供的原链接。按 [平台清单](references/sms-patterns.json) 分类。全国法院统一送达平台有下载与内容校验脚本。其他已识别的平台依赖浏览器和用户验证。 |
| PDF 与 OCR | 本地脚本读取文字层。扫描、乱码或关键页缺字时需可用的 [MinerU 凭据](references/mineru-ocr.md) 及网络。 |
| 飞书多维表格 | 宿主飞书连接或 Lark CLI 执行表字段查询、记录新增、更新、定向删除及结果回读。原件附件另需上传权限。 |
| 预警与自动化 | 新文书触发增量更新。剩余天数、填色和定时通知由目标 Base 的公式或自动化配置执行。可在飞书另行配置后台触发。 |
| 飞书日历与 Mac | 飞书日历需当前账号对目标日历有相应权限。Mac 日历和提醒依赖本机 GUI applet 和自动化授权。 |

Python 脚本建议用 Python 3.9 或更高版本。在 Skill 根目录安装 PDF 依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r scripts/requirements.txt
python3 -B -m unittest discover -s tests
```

测试使用合成短信和 PDF 响应，验证识别、下载校验、重复处理、日期和取消计划。

版权和参考项目见 [LICENSE.txt](LICENSE.txt) 与 [ATTRIBUTION.md](references/ATTRIBUTION.md)。
