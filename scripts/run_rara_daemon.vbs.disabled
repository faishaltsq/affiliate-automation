Set WshShell = CreateObject("WScript.Shell")
Dim projectDir, pythonExe, cmd

projectDir = "C:\Users\cubeb\OneDrive\Documents\coding\projects\affiliate-automation"
pythonExe = "C:\Users\cubeb\AppData\Local\hermes\installs\e53b2a297b9d149d\environments\144c22db562846a89a2d63db56e9fd49\venv\Scripts\python.exe"

cmd = """" & pythonExe & """ main.py organic --daemon"
WshShell.CurrentDirectory = projectDir
WshShell.Run cmd, 0, False
