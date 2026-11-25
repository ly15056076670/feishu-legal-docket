-- court-doc-extract: wb-calendar
-- 机制（v1.4.5 修复，复用 v1.2.1「无参数 applet + GUI 启动」设计）：
--   · 使用无参数的 `on run`（不再接收 argv，彻底抛弃 `open <app> --args`）。
--   · 从稳定、非 /tmp 的用户目录读取任务、写回执：
--       ~/Library/Application Support/CourtDocExtract/sync/calendar_task.txt
--       ~/Library/Application Support/CourtDocExtract/sync/calendar_result.txt
--     目录权限 0700、任务/回执文件权限 0600 由 Python 端负责创建与设定。
--   · 任务文件首行为 RUN_ID=<uuid>；本 applet 完成后向结果文件回写**相同** RUN_ID。
--   · 回执强制 UTF-8 写入（修复旧版中文错误信息乱码）。
--   · 逐条任务独立异常捕获；异常分支安全引用已初始化的 taskId。
--   · TCC 拒绝时保留原始 AppleScript 错误文本，绝不用「可能未授权」掩盖真实错误。
--
-- 任务文件格式（首行 RUN_ID，其后每行）：
--   标题|||YYYY-MM-DD HH:MM|||地点|||日历名|||描述(含 task_id)
-- 按 task_id（描述内 "task_id=..."）匹配既有本 Skill 日历事件：
--   命中且日期/标题/地点一致 → 跳过(已一致)；不一致 → 更新；未命中 → 新建。
-- 完成后向结果文件回写：RUN_ID= / created= / updated= / skipped= / failed= / fail=... / task:...
-- 安全边界：只处理 description 含 court-doc-extract-managed 标记的事件，绝不碰用户原有事项。

use scripting additions

on getSyncDir()
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
	set dateParts to text items of inputText
	set AppleScript's text item delimiters to "-"
	set yearMonthDay to text items of (item 1 of dateParts)
	set AppleScript's text item delimiters to ":"
	set hourMinute to text items of (item 2 of dateParts)
	set AppleScript's text item delimiters to ""
	set parsedDate to «event misccurd»
	set year of parsedDate to (item 1 of yearMonthDay) as integer
	set month of parsedDate to (item 2 of yearMonthDay) as integer
	set day of parsedDate to (item 3 of yearMonthDay) as integer
	set hours of parsedDate to (item 1 of hourMinute) as integer
	set minutes of parsedDate to (item 2 of hourMinute) as integer
	set seconds of parsedDate to 0
	return parsedDate
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
	set resultText to "RUN_ID=" & runId & linefeed & "created=" & c & linefeed & "updated=" & u & linefeed & "skipped=" & s & linefeed & "failed=" & fl
	repeat with x in fails
		set resultText to resultText & linefeed & "fail=" & x
	end repeat
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
	set syncDir to my getSyncDir()
	set taskFile to syncDir & "calendar_task.txt"
	set resultFile to syncDir & "calendar_result.txt"
	set managedMarker to "court-doc-extract-managed"
	set runId to ""

	set raw to ""
	-- v1.4.5 修复：弃用旧版「rdwrread 原始事件 + given as:utf8」读法；该写法依赖带尾随空格的
	-- 原始类码，易在编辑器/传输中丢失而编译报 -2741。改用可移植的 open-for-access + read fd as utf8。
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

	set AppleScript's text item delimiters to (character id 13)
	set crParts to text items of raw
	set AppleScript's text item delimiters to linefeed
	set raw to crParts as string
	set AppleScript's text item delimiters to linefeed
	set rawLines to text items of raw

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
	set taskId to "" -- 循环前初始化（修复「异常处理分支引用未初始化 taskId」）

	tell application "Calendar"
		try
			repeat with aLine in taskLines
				set taskId to "" -- 每条任务前再次初始化（防御）
				set aLine to my trimText(aLine)
				if (length of aLine) is 0 then
					-- 空行跳过
				else
					try
						set AppleScript's text item delimiters to "|||"
						set parts to text items of aLine
						set AppleScript's text item delimiters to ""
						set n to count of parts
						if n >= 5 then
							set evtTitle to my trimText(item 1 of parts)
							set inputDateText to my trimText(item 2 of parts)
							set evtLoc to my trimText(item 3 of parts)
							set calName to my trimText(item 4 of parts)
							set evtDesc to my trimText(item 5 of parts)
							if evtDesc does not contain managedMarker then set evtDesc to managedMarker & " " & evtDesc
							set taskId to my extractTaskId(evtDesc)
							if inputDateText is "CANCEL" then
								set isCancel to true
							else
								set isCancel to false
								set d to my parseDate(inputDateText)
								set endDate to d + (2 * 60 * 60) -- 排程预估；非法院确认的散庭时刻
							end if

								-- 只使用指定日历。创建失败交给本条任务的失败回执处理。
								set targetCal to missing value
								if exists («class wres» calName) then
									set targetCal to «class wres» calName
								else if isCancel is false then
									set targetCal to make new «class wres» with properties {«class pnam»:calName}
								end if

							-- 按 task_id 匹配既有本 Skill 事件（不依赖标题，标题含日期会变）
							set foundEv to missing value
							try
								set allEvs to {}
								if targetCal is not missing value then set allEvs to every «class wrev» of targetCal
								repeat with e in allEvs
									try
										set ed to «class wr12» of e as string
									on error
										set ed to ""
									end try
									if ed contains managedMarker and ed contains taskId then
										set foundEv to e
										exit repeat
									end if
								end repeat
							on error
								set foundEv to missing value
							end try

							if isCancel then
								if foundEv is missing value then
									set skippedCount to skippedCount + 1
									set taskReceipt to taskReceipt & {taskId & " skipped"}
								else
									delete foundEv
									set updatedCount to updatedCount + 1
									set taskReceipt to taskReceipt & {taskId & " updated"}
								end if
							else if foundEv is missing value then
								-- 新建（描述携带 task_id，供下次更新定位）
								make new «class wrev» at end of every «class wrev» of targetCal with properties {«class wr11»:evtTitle, «class wr1s»:d, «class wr5s»:endDate, «class wr14»:evtLoc, «class wr12»:evtDesc}
								set createdCount to createdCount + 1
								set taskReceipt to taskReceipt & {taskId & " created"}
							else
								-- 命中：日期/标题/地点不一致则更新，否则跳过
								set needUpdate to false
								try
									set exStart to «class wr1s» of foundEv
									if (exStart as string) is not (d as string) then set needUpdate to true
								end try
								try
									set exEnd to «class wr5s» of foundEv
									if (exEnd as string) is not (endDate as string) then set needUpdate to true
								end try
								try
									set exLoc to «class wr14» of foundEv as string
									if exLoc is not evtLoc then set needUpdate to true
								end try
								try
									set exSum to «class wr11» of foundEv as string
									if exSum is not evtTitle then set needUpdate to true
								end try
								if needUpdate then
									set «class wr11» of foundEv to evtTitle
									set «class wr14» of foundEv to evtLoc
									set «class wr12» of foundEv to evtDesc
									-- 避免 Calendar 在顺序赋值时因 start>=end 校验失败：先放宽结束日期，再设起止
									set «class wr5s» of foundEv to (current date) + (100 * days)
									set «class wr1s» of foundEv to d
									set «class wr5s» of foundEv to endDate
									set updatedCount to updatedCount + 1
									set taskReceipt to taskReceipt & {taskId & " updated"}
								else
									set skippedCount to skippedCount + 1
									set taskReceipt to taskReceipt & {taskId & " skipped"}
								end if
							end if
						end if
					on error errMsg
						set failedCount to failedCount + 1
						set failureList to failureList & (aLine & "：" & errMsg)
						if taskId is "" then set taskId to "unknown"
						set taskReceipt to taskReceipt & {taskId & " failed"}
					end try
				end if
			end repeat
		on error errMsg
			set remaining to totalCount - createdCount - updatedCount - skippedCount - failedCount
			if remaining < 0 then set remaining to 0
			set failedCount to failedCount + remaining
			set failureList to failureList & ("Calendar 访问失败（原始错误：" & errMsg & "）")
		end try
	end tell

	my writeResult(resultFile, runId, createdCount, updatedCount, skippedCount, failedCount, failureList, taskReceipt)
end run
