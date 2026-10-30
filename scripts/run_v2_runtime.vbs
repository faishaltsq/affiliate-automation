' Affiliate Automation V2 — content runtime (persona-first Threads loop)
' Launched by scripts/watchdog.py. Runs the scheduler in the foreground of
' this detached process: generate -> gate -> queue -> (publish if approved).
Option Explicit
Dim WshShell, projectDir, pythonExe, cmd
Set WshShell = CreateObject("WScript.Shell")
projectDir = "C:\Users\cubeb\OneDrive\Documents\coding\projects\affiliate-automation"
pythonExe = "C:\Users\cubeb\AppData\Local\hermes\installs\e53b2a297b9d149d\environments\144c22db562846a89a2d63db56e9fd49\venv\Scripts\python.exe"
cmd = """" & pythonExe & """ -m src.v2_runtime"
WshShell.CurrentDirectory = projectDir
WshShell.Run cmd, 0, False
