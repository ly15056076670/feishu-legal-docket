# Mac 提醒事项与日历同步

复用用户给出的 `court-doc-extract` 原包 AppleScript GUI applet（`scripts/macos/`），包装为本技能的 `scripts/mac_sync.py`。需要在**要写入提醒的那台 Mac**运行；仅在 macOS、系统本地时区 UTC+8 且用户许可日历／提醒事项访问时实际同步。其他执行环境只用 `--dry-run` 预览并明确报告没有写 Mac；飞书连接不等于 Mac 权限。

## 输入与门禁

把原件提取并经律师复核的事项写为当前任务的临时 JSON，示例：

```json
{
  "items": [
    {"kind":"hearing", "case_no":"（2026）虚0101民初1003号", "node_key":"开庭-01", "title":"开庭：陈一诉吴二", "at":"2026-10-23 10:00", "location":"第三模拟审判庭", "source":"传票第1页", "confirmed":true},
    {"kind":"deadline", "case_no":"（2026）虚0101民初1003号", "node_key":"举证期限-该份通知", "title":"举证截止", "at":"2026-10-19", "source":"举证通知书第1页；律师确认送达日", "confirmed":true}
  ]
}
```

`node_key` 必须在改期、日期更正后仍保持不变（飞书记录 ID 或案件内固定节点标识），不同财产/不同期限不能重用；稳定 `task_id` 据此计算，重试更新本技能旧项。`confirmed=true` 仅限有明确文书记载／律师复核过的节点；`status` 不是 `effective`、时间不明确或已过期的节点拒绝创建。明确的开庭取消传入 `kind=hearing, status=cancelled` 并保留相同 `case_no/node_key`，定向完成原提醒、删除仅带本技能标记的旧 Mac 日历事件；不据模糊改期直接取消。日期只有年月日而无时刻时，提醒预设本地 09:00，并在备注说明这是**提醒时间**而非法院指定时刻；开庭必须有实际时刻。

已核实的开庭取消或续保也必须传入 JSON 布尔值 `"confirmed": true`。续保使用 JSON 布尔值 `"renewed": true`。同一批次内，相同节点的相同操作只提交一次。同一节点出现相互冲突的日期、内容或操作时，该节点暂缓同步，待选定有效版本。其他节点可以继续处理。

保全 `kind=preservation` 且 `renewed=false` 时，在到期前 60 日、30 日及到期日生成提醒；已过的提前节点跳过。`renewed=true` 时为同一 `node_key` 输出 `CANCEL` 定向任务，GUI applet 仅将带本技能标记且匹配 `task_id` 的旧提醒设为已完成，不动其他提醒；核对回执以确认停用。若尚未完成续保，无证据不得根据计划自行置 `renewed=true`。

## 执行

```bash
bash scripts/macos/setup_apps.sh
python3 scripts/mac_sync.py --input '<本次任务的事项.json>' --dry-run
python3 scripts/mac_sync.py --input '<本次任务的事项.json>'
```

预览不写任何任务文件或 Mac 内容。真实运行使用原包固定目录 `~/Library/Application Support/CourtDocExtract/sync/`，原子生成 `0600` 任务文件并加锁；通过 GUI applet `open -W` 请求系统权限，核验本次 RUN_ID 与成功／失败逐项回执。没有一致回执即报告未完成，不能把“命令已经调用”称作成功。**开庭**写“开庭”提醒列表和 Mac 日历“开庭”（无结束时间时按开庭后 2 小时作为排程预估）；**其他期限仅写“♾️重要期限提醒”列表**。Mac 日历可能受用户默认警报设置影响，首次创建后应核对具体事件；不要把 Mac 日历和飞书日历混报。变更、取消开庭需对同一稳定节点定向更新或取消旧提醒/日程；若旧事件不能可靠取消，报告未完成并请用户检查，不能并存两个有效开庭。

不要运行原包 `wb-clear.app` 做常规同步；它可能清理其他由旧技能创建的带标记事项。只在用户明确要求清理时使用。
