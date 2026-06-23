Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c cd C:\Users\HRIS\Downloads\EngineAI && uv run folder_watcher ""C:\Shared_PDF_Folder"" ""MSI-WILLYPC\SQLEXPRESS"" ""PdfExtractionRecords""", 0, False
