-- Bookmark WWDC26 performance sessions in Developer.app (Cmd+/).
-- Run: osascript ops/bin/bookmark_apple_perf_videos.applescript

property videoURLs : {¬
	"https://developer.apple.com/videos/play/wwdc2026/268/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/243/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/222/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/269/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/321/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/303/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/8003/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/8041/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/8082/", ¬
	"https://developer.apple.com/videos/play/wwdc2026/416/"}

on bookmarkVideo(u)
	tell application "Developer"
		activate
		open location u
	end tell
	delay 4
	tell application "System Events"
		tell process "Developer"
			set frontmost to true
			keystroke "/" using command down
		end tell
	end tell
	delay 1
end bookmarkVideo

on run
	repeat with u in videoURLs
		my bookmarkVideo(u)
	end repeat
	tell application "Developer" to activate
	delay 0.5
	tell application "System Events"
		tell process "Developer"
			set frontmost to true
			keystroke "2" using command down
		end tell
	end tell
	return videoURLs
end run
