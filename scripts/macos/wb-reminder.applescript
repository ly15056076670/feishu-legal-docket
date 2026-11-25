-- court-doc-extract: wb-reminder
-- 机制（v1.4.5 修复，复用 v1.2.1「无参数 applet + GUI 启动」设计）：
--   · 使用无参数的 `on run`（不再接收 argv，彻底抛弃 `open <app> --args`，
--     该方式在 macOS 上无法可靠把参数传给 AppleScript applet 的 on run argv）。
--   · 从稳定、非 /tmp 的用户目录读取任务、写回执：
--       ~/Library/Application Support/CourtDocExtract/sync/reminder_task.txt
--       ~/Library/Application Support/CourtDocExtract/sync/reminder_result.txt
--     目录权限 0700、任务/回执文件权限 0600 由 Python 端负责创建与设定。
--   · 任务文件首行为 RUN_ID=<uuid>；本 applet 完成后向结果文件回写**相同** RUN_ID，
--     供 Python 端校验「回执确属本次运行」、拒绝旧回执误判。
--   · 回执强制 UTF-8 写入（修复旧版中文错误信息乱码）。
--   · 逐条任务独立异常捕获：一条失败不影响其余任务处理；异常分支安全引用已初始化的 taskId。
--   · TCC 拒绝时保留原始 AppleScript 错误文本，绝不用「可能未授权」掩盖真实错误。
--
-- 任务文件格式（首行 RUN_ID，其后每行）：
--   标题|||YYYY-MM-DD HH:MM|||列表名|||备注(含 task_id)
-- 按 task_id（备注内 "task_id=..."）匹配既有本 Skill 提醒事项：
--   命中且日期一致 → 跳过(已一致)；命中但日期/标题不一致 → 更新；未命中 → 新建。
-- 完成后向结果文件回写：RUN_ID= / created= / updated= / skipped= / failed= / fail=... / task:...
-- 安全边界：只处理 body 含 court-doc-extract-managed 标记的事项，绝不碰用户原有事项。

use scripting additions

on getSyncDir()
	-- 稳定、非 /tmp 的用户目录；POSIX 路径，供 POSIX file 读写使用。
	return (POSIX path of (path to application support folder from user domain)) & "CourtDocExtract/sync/"
end getSyncDir

on trimText(t)
	set t to t as string
	repeat while (length of t) > 0 and t begins with " "
		if (length of t) is 1 then return ""
		set t to text 2 thru -1 of t
	end repeat
	repeat while (length of t) > 0 and t ends with " "
		if (length of t) is 1 then return ""
		set t to text 1 thru -2 of t
	end repeat
	return t
end trimText

on parseDate(inputText)
	set AppleScript's text item delimiters to " "
	set dparts to text items of inputText
	set AppleScript's text item delimiters to "-"
	set ymd to text items of (item 1 of dparts)
	set AppleScript's text item delimiters to ":"
	set hm to text items of (item 2 of dparts)
	set AppleScript's text item delimiters to ""
	set d to «event misccurd»
	set year of d to (item 1 of ymd) as integer
	set month of d to (item 2 of ymd) as integer
	set day of d to (item 3 of ymd) as integer
	set hours of d to (item 1 of hm) as integer
	set minutes of d to (item 2 of hm) as integer
	set seconds of d to 0
	return d
end parseDate

on extractTaskId(s)
	set AppleScript's text item delimiters to "task_id="
	set parts to text items of s
	if (count of parts) < 2 then return ""
	set tail to item 2 of parts
	set AppleScript's text item delimiters to " "
	set sub to text items of tail
	set AppleScript's text item delimiters to ""
	return item 1 of sub
end extractTaskId

on writeResult(f, runId, c, u, s, fl, fails, receiptList)
	-- 回执强制 UTF-8（写入用 `write ... as «class utf8»`），中文错误信息不再乱码。
	set resultText to "RUN_ID=" & runId & linefeed & "created=" & c & linefeed & "updated=" & u & linefeed & "skipped=" & s & linefeed & "failed=" & fl
	repeat with x in fails
		set resultText to resultText & linefeed & "fail=" & x
	end repeat
	-- 逐项 task_id 回执（Python 端据 task_id 归因为具体案号/节点）
	repeat with e in receiptList
		set resultText to resultText & linefeed & "task:" & e
	end repeat
	try
		set fd to open for access (POSIX file f) with write permission
		set eof of fd to 0
		write resultText to fd as «class utf8»
		close access fd
	on error
		try
			close access (POSIX file f)
		end try
	end try
end writeResult

on run
	-- 无参数 on run：从固定同步目录读取任务、写回执（不接收任何 argv）。
	set syncDir to my getSyncDir()
	set taskFile to syncDir & "reminder_task.txt"
	set resultFile to syncDir & "reminder_result.txt"
	set managedMarker to "court-doc-extract-managed"
	set runId to ""

	-- 读取任务文件（UTF-8）
	-- v1.4.5 修复：弃用旧版「rdwrread 原始事件 + given as:utf8」读法；该写法依赖带尾随空格的
	-- 原始类码，易在编辑器/传输中丢失而编译报 -2741。改用可移植的 open-for-access + read fd as utf8，
	-- 编译稳定且无需 TCC 授权。
	set raw to ""
	try
		set fd to open for access (POSIX file taskFile)
		set raw to read fd as «class utf8»
		close access fd
	on error errMsg
		try
			close access (POSIX file taskFile)
		end try
		my writeResult(resultFile, runId, 0, 0, 0, 1, {"无法读取任务文件：" & taskFile & " | " & errMsg}, {})
		return
	end try

	-- 归一化换行符（CR/LF 统一为 linefeed）
	set AppleScript's text item delimiters to (character id 13)
	set crParts to text items of raw
	set AppleScript's text item delimiters to linefeed
	set raw to crParts as string
	set AppleScript's text item delimiters to linefeed
	set rawLines to text items of raw

	-- 解析首行 RUN_ID=<uuid>；其后各行才是任务行（兼容旧格式：无 RUN_ID 时全部当任务行）
	set taskLines to {}
	set runIdFound to false
	repeat with i from 1 to count of rawLines
		set ln to item i of rawLines
		if not runIdFound and ln starts with "RUN_ID=" then
			set runId to text 8 thru -1 of ln
			set runIdFound to true
		else if runIdFound then
			set end of taskLines to ln
		end if
	end repeat
	if not runIdFound then
		set taskLines to rawLines
	end if

	set totalCount to count of taskLines
	set createdCount to 0
	set updatedCount to 0
	set skippedCount to 0
	set failedCount to 0
	set failureList to {}
	set taskReceipt to {}
	set taskId to "" -- 循环前初始化（修复「异常处理分支引用未初始化 taskId」导致真实错误被二次覆盖）

	tell application "Reminders"
		try
			repeat with aLine in taskLines
				set taskId to "" -- 每条任务前再次初始化（防御：异常分支安全引用）
				set aLine to my trimText(aLine)
				if (length of aLine) is 0 then
					-- 空行不计入（已在 Python 侧去重/过滤）
				else
					try
						set AppleScript's text item delimiters to "|||"
						set parts to text items of aLine
						set AppleScript's text item delimiters to ""
						set n to count of parts
						if n >= 4 then
							set remTitle to my trimText(item 1 of parts)
							set inputDateText to my trimText(item 2 of parts)
							set listName to my trimText(item 3 of parts)
							set bodyText to my trimText(item 4 of parts)
							if bodyText does not contain managedMarker then set bodyText to managedMarker & " " & bodyText
							set taskId to my extractTaskId(bodyText)
							if inputDateText is "CANCEL" then
								set isCancel to true
							else
								set isCancel to false
								set d to my parseDate(inputDateText)
								set remindDate to d
							end if

								-- 只使用指定列表。创建失败交给本条任务的失败回执处理。
								set targetList to missing value
								if exists («class list» listName) then
									set targetList to «class list» listName
								else if isCancel is false then
									set targetList to make new «class list» with properties {«class pnam»:listName}
								end if

							-- 按 task_id 匹配既有本 Skill 提醒（不依赖标题，标题含日期会变）
							set foundRem to missing value
							try
								set allRems to {}
								if targetList is not missing value then set allRems to every «class remi» of targetList
								repeat with r in allRems
									try
										set rb to «class body» of r as string
									on error
										set rb to ""
									end try
									if rb contains managedMarker and rb contains taskId then
										set foundRem to r
										exit repeat
									end if
								end repeat
							on error
								set foundRem to missing value
							end try

							if isCancel then
								if foundRem is missing value then
									set skippedCount to skippedCount + 1
									set taskReceipt to taskReceipt & {taskId & " skipped"}
								else
									set completed of foundRem to true
									set updatedCount to updatedCount + 1
									set taskReceipt to taskReceipt & {taskId & " updated"}
								end if
							else if foundRem is missing value then
								-- 新建
								tell targetList
									make new «class remi» with properties {«class pnam»:remTitle, «class dued»:d, «class rmdt»:remindDate, «class body»:bodyText}
								end tell
								set createdCount to createdCount + 1
								set taskReceipt to taskReceipt & {taskId & " created"}
							else
								-- 命中：日期/标题/正文任一不一致则更新（不产生旧项残留）
								set needUpdate to false
								try
									set exDue to «class dued» of foundRem
									if (exDue as string) is not (d as string) then set needUpdate to true
								end try
								try
									set exRemind to «class rmdt» of foundRem
									if (exRemind as string) is not (remindDate as string) then set needUpdate to true
								end try
								-- v1.5.2 补丁：标题变化（如法院简称/审判区修正）也要触发更新
								try
									set exTitle to «class pnam» of foundRem as string
									if exTitle is not remTitle then set needUpdate to true
								end try
								try
									set exBody to «class body» of foundRem as string
									if exBody is not bodyText then set needUpdate to true
								end try
								try
									if completed of foundRem is true then set needUpdate to true
								end try
								if needUpdate then
									set completed of foundRem to false
									set «class pnam» of foundRem to remTitle
									set «class dued» of foundRem to d
									set «class rmdt» of foundRem to remindDate
									set «class body» of foundRem to bodyText
									set updatedCount to updatedCount + 1
									set taskReceipt to taskReceipt & {taskId & " updated"}
								else
									set skippedCount to skippedCount + 1
									set taskReceipt to taskReceipt & {taskId & " skipped"}
								end if
							end if
						end if
					on error errMsg
						-- 逐条独立异常：本条失败标记 failed，taskId 已初始化不会未定义
						set failedCount to failedCount + 1
						set failureList to failureList & (aLine & "：" & errMsg)
						if taskId is "" then set taskId to "unknown"
						set taskReceipt to taskReceipt & {taskId & " failed"}
					end try
				end if
			end repeat
		on error errMsg
			-- 整块失败（典型：TCC 权限被拒）→ 未处理的条目全部计失败
			set remaining to totalCount - createdCount - updatedCount - skippedCount - failedCount
			if remaining < 0 then set remaining to 0
			set failedCount to failedCount + remaining
			-- 保留原始 AppleScript 错误文本，不得用「可能未授权」掩盖真实错误
			set failureList to failureList & ("Reminders 访问失败（原始错误：" & errMsg & "）")
		end try
	end tell

	my writeResult(resultFile, runId, createdCount, updatedCount, skippedCount, failedCount, failureList, taskReceipt)
end run
