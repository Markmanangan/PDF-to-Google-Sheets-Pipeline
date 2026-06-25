' Launches the PDF folder watcher hidden in the background.
'
' Argument order for folder_watcher is:
'   <base_folder> <server> <database> [<table_name>]
'
' Previously the table name was passed in the DATABASE slot, which caused the
' watcher to create and write to a stray database named "PdfExtractionRecords"
' instead of "PdfPipelineDB". Fixed: database = PdfPipelineDB, table = PdfExtractionRecords.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c cd C:\Users\HRIS\Downloads\EngineAI && uv run folder_watcher ""C:\Shared_PDF_Folder"" ""MSI-WILLYPC\SQLEXPRESS"" ""PdfPipelineDB"" ""PdfExtractionRecords""", 0, False
