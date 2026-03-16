Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
tester = base & "\windows_tester.exe"

a = sh.Run("""" & tester & """", 1, False)

WScript.Sleep 400
On Error Resume Next
For i = 1 To 12
    sh.AppActivate "CSAD Windows Tester"
    WScript.Sleep 250
Next
On Error GoTo 0
