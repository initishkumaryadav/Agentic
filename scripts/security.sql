
IF OBJECT_ID(N'llm_views.Active_FLEET', N'V') IS NOT NULL
    DROP VIEW llm_views.Active_FLEET;
GO

IF USER_ID(N'llm') IS NOT NULL
    DROP USER llm;
GO

IF SUSER_ID(N'llm') IS NOT NULL
    DROP LOGIN llm;
GO

IF SCHEMA_ID(N'llm_views') IS NULL
    EXEC('CREATE SCHEMA llm_views;');
GO

CREATE VIEW llm_views.Active_FLEET AS
SELECT
    TS_UTC AS [Timestamp],
    V_Lat AS [Latitude],
    V_Lon AS [Longitude],
    CAST(IOT_TEMP_VAL_C AS FLOAT) AS [Current_Temperature_C],
    CGO_COND_CD AS [Cargo_Condition_Code],
    RISK_CLS_TXT AS [Risk_Classification],
    DELAY_PROB_DEC AS [Delay_Probability],
    PRT_CNG_LVL AS [Port_Congestion_Level],
    RT_RSK_IDX AS [Route_Risk_Index]
FROM dbo.RAW_DATA;
GO

IF OBJECT_ID(N'llm_views.AgentAuditLog', N'U') IS NULL
    CREATE TABLE llm_views.AgentAuditLog (
        LogID        INT IDENTITY(1,1) PRIMARY KEY,
        [Timestamp]  DATETIME2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),
        SessionID    NVARCHAR(64)   NOT NULL,
        NodeExecuted NVARCHAR(64)   NOT NULL,
        ToolName     NVARCHAR(128)  NULL,
        Content      NVARCHAR(MAX)  NULL
    );
GO


CREATE LOGIN llm WITH PASSWORD = 'Logicalone@';
CREATE USER llm FOR LOGIN llm;
GO

GRANT SELECT ON llm_views.Active_FLEET TO llm;
DENY SELECT ON dbo.RAW_DATA TO llm;
DENY INSERT, UPDATE, DELETE, ALTER ON SCHEMA::dbo TO llm;

GRANT INSERT ON llm_views.AgentAuditLog TO llm;
DENY SELECT, UPDATE, DELETE ON llm_views.AgentAuditLog TO llm;
GO
