-- ============================================================================
-- PdfPipelineDB setup script
-- ----------------------------------------------------------------------------
-- Creates the database, the dbo.PdfExtractionRecords table, and the three
-- stored procedures that sql_server_helper.py depends on:
--
--   [master].[dbo].[sp_EnsureDatabaseExists]
--       -> sql_server_helper.ensure_database_exists()
--   [PdfPipelineDB].[dbo].[sp_EnsureTableExists]
--       -> sql_server_helper.ensure_table_exists()
--   [PdfPipelineDB].[dbo].[sp_InsertExtractionRecord]
--       -> sql_server_helper.insert_extraction_record()
--
-- How to run:
--   1. Open SSMS and connect to your server (e.g. MSI-WILLYPC\SQLEXPRESS)
--      using Windows Authentication.
--   2. Open this file and execute it (F5). It is fully idempotent, so it is
--      safe to re-run.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Database
-- ----------------------------------------------------------------------------
IF DB_ID(N'PdfPipelineDB') IS NULL
BEGIN
    CREATE DATABASE PdfPipelineDB;
END
GO


-- ----------------------------------------------------------------------------
-- 2. Table dbo.PdfExtractionRecords
-- ----------------------------------------------------------------------------
USE PdfPipelineDB;
GO

IF OBJECT_ID(N'dbo.PdfExtractionRecords', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.PdfExtractionRecords
    (
        Id                    INT             IDENTITY(1,1) NOT NULL PRIMARY KEY,
        DocumentName          NVARCHAR(512)   NOT NULL,
        SourceFilePath        NVARCHAR(1024)  NOT NULL,
        ExtractionTimestamp   DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
        ValidationStatus      NVARCHAR(50)    NOT NULL DEFAULT 'Pending',
        ValidationNotes       NVARCHAR(MAX)   NULL,
        ExtractedRowsJson     NVARCHAR(MAX)   NOT NULL,
        ExcelPath             NVARCHAR(1024)  NULL,
        ProcessedAt           DATETIME2       NULL
    );
END
GO


-- ----------------------------------------------------------------------------
-- 3. Stored procedure: sp_EnsureDatabaseExists
--    Lives in [master] because it must be callable before the target database
--    exists, and it issues CREATE DATABASE (which must be the only statement
--    in its own batch / wrapped in EXEC).
-- ----------------------------------------------------------------------------
USE [master];
GO

IF OBJECT_ID(N'dbo.sp_EnsureDatabaseExists', N'P') IS NULL
BEGIN
    EXEC(N'CREATE PROCEDURE dbo.sp_EnsureDatabaseExists AS BEGIN SET NOCOUNT ON; END;');
END
GO

ALTER PROCEDURE dbo.sp_EnsureDatabaseExists
    @DatabaseName NVARCHAR(128)
AS
BEGIN
    SET NOCOUNT ON;

    IF @DatabaseName IS NULL OR LTRIM(RTRIM(@DatabaseName)) = N''
    BEGIN
        RAISERROR('sp_EnsureDatabaseExists: @DatabaseName is required.', 16, 1);
        RETURN;
    END

    IF @DatabaseName = N'master' RETURN;

    IF DB_ID(@DatabaseName) IS NULL
    BEGIN
        DECLARE @sql NVARCHAR(MAX) = N'CREATE DATABASE ' + QUOTENAME(@DatabaseName) + N';';
        EXEC sp_executesql @sql;
    END
END
GO


-- ----------------------------------------------------------------------------
-- 4. Stored procedure: sp_EnsureTableExists
--    Lives in [PdfPipelineDB]. The table schema is fixed (see section 2), but
--    the target table NAME is parameterised so the pipeline can target
--    dbo.PdfExtractionRecords or any renamed copy of it.
-- ----------------------------------------------------------------------------
USE PdfPipelineDB;
GO

IF OBJECT_ID(N'dbo.sp_EnsureTableExists', N'P') IS NULL
BEGIN
    EXEC(N'CREATE PROCEDURE dbo.sp_EnsureTableExists AS BEGIN SET NOCOUNT ON; END;');
END
GO

ALTER PROCEDURE dbo.sp_EnsureTableExists
    @TableName NVARCHAR(128)
AS
BEGIN
    SET NOCOUNT ON;

    IF @TableName IS NULL OR LTRIM(RTRIM(@TableName)) = N''
    BEGIN
        RAISERROR('sp_EnsureTableExists: @TableName is required.', 16, 1);
        RETURN;
    END

    IF OBJECT_ID(QUOTENAME(@TableName), N'U') IS NULL
    BEGIN
        DECLARE @sql NVARCHAR(MAX) = N'
        CREATE TABLE ' + QUOTENAME(@TableName) + N'
        (
            Id                    INT             IDENTITY(1,1) NOT NULL PRIMARY KEY,
            DocumentName          NVARCHAR(512)   NOT NULL,
            SourceFilePath        NVARCHAR(1024)  NOT NULL,
            ExtractionTimestamp   DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
            ValidationStatus      NVARCHAR(50)    NOT NULL DEFAULT ''Pending'',
            ValidationNotes       NVARCHAR(MAX)   NULL,
            ExtractedRowsJson     NVARCHAR(MAX)   NOT NULL,
            ExcelPath             NVARCHAR(1024)  NULL,
            ProcessedAt           DATETIME2       NULL
        );';

        EXEC sp_executesql @sql;
    END
END
GO


-- ----------------------------------------------------------------------------
-- 5. Stored procedure: sp_InsertExtractionRecord
--    Inserts one extraction record and returns the new Id via SELECT.
--    Parameters match sql_server_helper.insert_extraction_record() exactly.
-- ----------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.sp_InsertExtractionRecord', N'P') IS NULL
BEGIN
    EXEC(N'CREATE PROCEDURE dbo.sp_InsertExtractionRecord AS BEGIN SET NOCOUNT ON; END;');
END
GO

ALTER PROCEDURE dbo.sp_InsertExtractionRecord
    @TableName         NVARCHAR(128),
    @DocumentName      NVARCHAR(512),
    @SourceFilePath    NVARCHAR(1024),
    @ValidationStatus  NVARCHAR(50),
    @ValidationNotes   NVARCHAR(MAX) = NULL,
    @ExtractedRowsJson NVARCHAR(MAX),
    @ExcelPath         NVARCHAR(1024) = NULL,
    @ProcessedAt       DATETIME2 = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @ProcessedAt IS NULL
        SET @ProcessedAt = SYSUTCDATETIME();

    DECLARE @sql NVARCHAR(MAX) = N'
    INSERT INTO ' + QUOTENAME(@TableName) + N'
        (DocumentName, SourceFilePath, ValidationStatus, ValidationNotes,
         ExtractedRowsJson, ExcelPath, ProcessedAt)
    VALUES
        (@DocumentName, @SourceFilePath, @ValidationStatus, @ValidationNotes,
         @ExtractedRowsJson, @ExcelPath, @ProcessedAt);

    SELECT SCOPE_IDENTITY() AS Id;';

    EXEC sp_executesql
        @sql,
        N'@DocumentName NVARCHAR(512), @SourceFilePath NVARCHAR(1024),
          @ValidationStatus NVARCHAR(50), @ValidationNotes NVARCHAR(MAX),
          @ExtractedRowsJson NVARCHAR(MAX), @ExcelPath NVARCHAR(1024),
          @ProcessedAt DATETIME2',
        @DocumentName, @SourceFilePath, @ValidationStatus, @ValidationNotes,
        @ExtractedRowsJson, @ExcelPath, @ProcessedAt;
END
GO


-- ----------------------------------------------------------------------------
-- Done. Quick sanity check (optional, safe to leave in):
-- ----------------------------------------------------------------------------
USE PdfPipelineDB;
GO
SELECT name, type_desc FROM sys.objects
WHERE type IN (N'P', N'U') AND name LIKE N'%PdfExtractionRecords%' OR name LIKE N'sp_%'
ORDER BY type, name;
