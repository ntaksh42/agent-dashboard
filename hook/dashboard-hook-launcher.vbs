Option Explicit

If WScript.Arguments.Count < 1 Or WScript.Arguments.Count > 2 Then WScript.Quit 87

Dim shell, fso, inputFile, inputText, scriptPath, tool, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptPath = WScript.Arguments.Item(0)
tool = ""
If WScript.Arguments.Count = 2 Then tool = WScript.Arguments.Item(1)
inputText = WScript.StdIn.ReadAll
inputFile = fso.BuildPath(shell.ExpandEnvironmentStrings("%TEMP%"), fso.GetTempName)

Dim stream
Set stream = fso.CreateTextFile(inputFile, True, True)
stream.Write inputText
stream.Close

command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " & Quote(scriptPath)
If tool <> "" Then command = command & " -Tool " & Quote(tool) & " -InputPath " & Quote(inputFile)
exitCode = shell.Run(command, 0, True)

If fso.FileExists(inputFile) Then fso.DeleteFile inputFile, True
WScript.Quit exitCode

Function Quote(value)
    Quote = Chr(34) & Replace(value, Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function
