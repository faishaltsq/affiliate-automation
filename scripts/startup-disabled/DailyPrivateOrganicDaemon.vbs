' Daily Private Agent - Organic Threads Daemon (@pochita9887) - Windows Startup Launcher
Option Explicit
Dim fso, sh, target

target = "C:\Users\cubeb\OneDrive\Documents\coding\projects\affiliate-automation\scripts\run_rara_daemon.vbs"
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(target) Then WScript.Quit 0

Set sh = CreateObject("WScript.Shell")
sh.Run "wscript.exe """ & target & """", 0, False
