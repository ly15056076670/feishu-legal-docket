-- court-doc-extract 安全清理工具
-- 只删除 body/description 含专属标记的事项，不删除任何列表或日历。

on run
	set managedMarker to "court-doc-extract-managed"
	set reminderHit to 0
	set calendarHit to 0

	tell application "Reminders"
		repeat with targetList in every «class list»
			try
				set itemsToCheck to every «class remi» of targetList
				repeat with anItem in itemsToCheck
					try
						set itemBody to «class body» of anItem as string
						if itemBody contains managedMarker then
							delete anItem
							set reminderHit to reminderHit + 1
						end if
					end try
				end repeat
			end try
		end repeat
	end tell

	tell application "Calendar"
		repeat with targetCal in every «class wres»
			try
				set eventsToCheck to every «class wrev» of targetCal
				repeat with anEvent in eventsToCheck
					try
						set eventDescription to «class wr12» of anEvent as string
						if eventDescription contains managedMarker then
							delete anEvent
							set calendarHit to calendarHit + 1
						end if
					end try
				end repeat
			end try
		end repeat
	end tell

	return "已删除本 Skill 创建的提醒 " & reminderHit & " 条、日历事件 " & calendarHit & " 条；未删除任何列表或日历。"
end run