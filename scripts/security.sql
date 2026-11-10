
IF OBJECT_ID(N'llm_views.Active_FLEET', N'V') IS NOT NULL
    DROP VIEW llm_views.Active_FLEET;
GO

-- Drop schema if it exists
IF SCHEMA_ID(N'llm_views') IS NOT NULL
    EXEC('DROP SCHEMA llm_views;');
GO
IF USER_ID(N'llm') IS NOT NULL
    DROP USER llm;
GO

IF SUSER_ID(N'llm') IS NOT NULL
    DROP LOGIN llm;
GO

CREATE SCHEMA llm_views;
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
FROM dbo.TBL_SC_FLEET_HIST_RAW;
GO


CREATE LOGIN llm WITH PASSWORD = 'Logicalone@';
CREATE USER llm FOR LOGIN llm;
GO

GRANT SELECT ON llm_views.Active_FLEET TO llm;
DENY SELECT ON dbo.TBL_SC_FLEET_HIST_RAW TO llm;
DENY INSERT, UPDATE, DELETE, ALTER ON SCHEMA::dbo TO llm;
GO