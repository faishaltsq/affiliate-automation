' Daily Private Agent Bot (@dailyprivateagentbot) - Windows Startup Launcher
Option Explicit
Dim fso, sh, target

target = "C:\Users\cubeb\OneDrive\Documents\coding\projects\affiliate-automation\scripts\run_telegram_bot.vbs"
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(target) Then WScript.Quit 0

Set sh = CreateObject("WScript.Shell")
sh.Run "wscript.exe """ & target & """", 0, False
