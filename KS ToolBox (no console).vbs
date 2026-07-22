' KS ToolBox - flash-free launcher (no console window at all).
' Double-click this instead of the .cmd if you don't want the brief console.
' (Run the .cmd once first if the venv isn't set up yet.)
Dim fso, sh, here, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
pyw = here & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pyw) Then
    sh.Run """" & pyw & """ main.py", 0, False   ' 0 = hidden, no console
Else
    ' first run: do the visible one-time setup via the .cmd
    sh.Run "cmd /c """ & here & "\KS ToolBox.cmd""", 1, False
End If
