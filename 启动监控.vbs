Option Explicit
Dim sh, fs, folder, pythonw, command, status
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = sh.Environment("PROCESS")("WIRELESS_BATTERY_PYTHON")
If pythonw = "" Then
  pythonw = folder & "\.venv\Scripts\pythonw.exe"
  If Not fs.FileExists(pythonw) Then pythonw = "D:\PPPPP\pythonw.exe"
  If Not fs.FileExists(pythonw) Then pythonw = "pythonw.exe"
End If
sh.CurrentDirectory = folder
sh.Environment("PROCESS")("PYTHONDONTWRITEBYTECODE") = "1"
command = Chr(34) & pythonw & Chr(34) & " -B " & Chr(34) & folder & "\main.py" & Chr(34)
On Error Resume Next
status = sh.Run(command, 0, False)
If Err.Number <> 0 Then
  MsgBox "Python could not be started. Install Python 3.10+ with tkinter, or set WIRELESS_BATTERY_PYTHON to pythonw.exe.", 16, "Wireless Battery Monitor"
  WScript.Quit 1
End If
