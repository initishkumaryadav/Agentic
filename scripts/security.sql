
EXEC sp_set_session_context N'agent_login',    N'llmagent';
EXEC sp_set_session_context N'agent_password', N'<AGENT PASSWORD>';
EXEC sp_set_session_context N'audit_login',    N'jailogic';
EXEC sp_set_session_context N'audit_password', N'<AUDIT PASSWORD>';
GO


DECLARE @agent sysname = CAST(SESSION_CONTEXT(N'agent_login') AS sysname),
        @agent_pw nvarchar(128) = CAST(SESSION_CONTEXT(N'agent_password') AS nvarchar(128)),
        @audit sysname = CAST(SESSION_CONTEXT(N'audit_login') AS sysname),
        @audit_pw nvarchar(128) = CAST(SESSION_CONTEXT(N'audit_password') AS nvarchar(128));
DECLARE @problem nvarchar(400) =
    CASE
        WHEN @agent IS NULL OR @agent_pw IS NULL OR @audit IS NULL OR @audit_pw IS NULL
            THEN N'STEP 1 did not run; run the whole file.'
        WHEN @agent_pw LIKE N'<%>' OR @audit_pw LIKE N'<%>' THEN N'Fill in both passwords in STEP 1.'
        WHEN @agent LIKE N'%[^A-Za-z0-9_]%' OR @audit LIKE N'%[^A-Za-z0-9_]%' THEN N'Login names may only use letters, digits and underscores.'
        WHEN @agent = @audit THEN N'Agent and audit logins must be different.'
        WHEN @agent = N'llm' OR @audit = N'llm' THEN N'''llm'' is the old shared login this script removes; use new names.'
        WHEN CHARINDEX(LOWER(@agent), LOWER(@agent_pw)) > 0 THEN N'The agent password must not contain the agent login name.'
        WHEN CHARINDEX(LOWER(@audit), LOWER(@audit_pw)) > 0 THEN N'The audit password must not contain the audit login name.'
    END;
IF @problem IS NOT NULL
BEGIN
    RAISERROR(N'STOPPED, nothing was changed: %s', 16, 1, @problem);
    SET NOEXEC ON;
END
GO


IF SCHEMA_ID(N'llm_views') IS NULL
    EXEC('CREATE SCHEMA llm_views AUTHORIZATION dbo;');
GO


CREATE OR ALTER VIEW llm_views.Active_FLEET AS
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
        UserID       NVARCHAR(256)  NULL,
        NodeExecuted NVARCHAR(64)   NOT NULL,
        ToolName     NVARCHAR(128)  NULL,
        Content      NVARCHAR(MAX)  NULL
    );
GO

IF COL_LENGTH(N'llm_views.AgentAuditLog', N'UserID') IS NULL
    ALTER TABLE llm_views.AgentAuditLog ADD UserID NVARCHAR(256) NULL;
GO

DECLARE @agent sysname = CAST(SESSION_CONTEXT(N'agent_login') AS sysname),
        @agent_pw nvarchar(128) = CAST(SESSION_CONTEXT(N'agent_password') AS nvarchar(128)),
        @audit sysname = CAST(SESSION_CONTEXT(N'audit_login') AS sysname),
        @audit_pw nvarchar(128) = CAST(SESSION_CONTEXT(N'audit_password') AS nvarchar(128));
DECLARE @a nvarchar(260) = QUOTENAME(@agent),
        @w nvarchar(260) = QUOTENAME(@audit),
        @sql nvarchar(max);

BEGIN TRY
    SET @sql = CASE WHEN SUSER_ID(@agent) IS NULL
        THEN N'CREATE LOGIN ' + @a + N' WITH PASSWORD = N''' + REPLACE(@agent_pw, N'''', N'''''') + N''', CHECK_POLICY = ON;'
        ELSE N'ALTER LOGIN ' + @a + N' WITH PASSWORD = N''' + REPLACE(@agent_pw, N'''', N'''''') + N''';' END;
    EXEC (@sql);
    SET @sql = CASE WHEN SUSER_ID(@audit) IS NULL
        THEN N'CREATE LOGIN ' + @w + N' WITH PASSWORD = N''' + REPLACE(@audit_pw, N'''', N'''''') + N''', CHECK_POLICY = ON;'
        ELSE N'ALTER LOGIN ' + @w + N' WITH PASSWORD = N''' + REPLACE(@audit_pw, N'''', N'''''') + N''';' END;
    EXEC (@sql);

    IF USER_ID(@agent) IS NULL EXEC (N'CREATE USER ' + @a + N' FOR LOGIN ' + @a + N';');
    IF USER_ID(@audit) IS NULL EXEC (N'CREATE USER ' + @w + N' FOR LOGIN ' + @w + N';');

    EXEC (N'GRANT SELECT ON llm_views.Active_FLEET TO ' + @a + N';
            DENY SELECT ON dbo.RAW_DATA TO ' + @a + N';
            DENY SELECT, INSERT, UPDATE, DELETE ON llm_views.AgentAuditLog TO ' + @a + N';
            DENY INSERT, UPDATE, DELETE, ALTER ON SCHEMA::dbo TO ' + @a + N';
            DENY INSERT, UPDATE, DELETE, ALTER ON SCHEMA::llm_views TO ' + @a + N';');


    EXEC (N'GRANT INSERT ON llm_views.AgentAuditLog TO ' + @w + N';
            DENY SELECT, UPDATE, DELETE, ALTER ON llm_views.AgentAuditLog TO ' + @w + N';
            DENY SELECT ON llm_views.Active_FLEET TO ' + @w + N';
            DENY SELECT, INSERT, UPDATE, DELETE, ALTER ON SCHEMA::dbo TO ' + @w + N';');
END TRY
BEGIN CATCH
    DECLARE @err nvarchar(2048) = ERROR_MESSAGE();
    RAISERROR(N'STOPPED at logins/permissions, old login ''llm'' was kept: %s', 16, 1, @err);
    SET NOEXEC ON;
END CATCH
GO

IF USER_ID(N'llm') IS NOT NULL
    DROP USER llm;
IF SUSER_ID(N'llm') IS NOT NULL
    DROP LOGIN llm;
GO


DECLARE @agent sysname = CAST(SESSION_CONTEXT(N'agent_login') AS sysname),
        @audit sysname = CAST(SESSION_CONTEXT(N'audit_login') AS sysname);
DECLARE @checks TABLE (Login sysname, Permission nvarchar(20), ObjectName nvarchar(128), Expected bit, Actual bit);

EXECUTE AS USER = @agent;
INSERT @checks VALUES
    (@agent, N'SELECT', N'llm_views.Active_FLEET',  1, HAS_PERMS_BY_NAME(N'llm_views.Active_FLEET', N'OBJECT', N'SELECT')),
    (@agent, N'SELECT', N'dbo.RAW_DATA',            0, HAS_PERMS_BY_NAME(N'dbo.RAW_DATA', N'OBJECT', N'SELECT')),
    (@agent, N'INSERT', N'llm_views.AgentAuditLog', 0, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'INSERT')),
    (@agent, N'SELECT', N'llm_views.AgentAuditLog', 0, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'SELECT')),
    (@agent, N'DELETE', N'llm_views.AgentAuditLog', 0, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'DELETE'));
REVERT;

EXECUTE AS USER = @audit;
INSERT @checks VALUES
    (@audit, N'INSERT', N'llm_views.AgentAuditLog', 1, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'INSERT')),
    (@audit, N'SELECT', N'llm_views.AgentAuditLog', 0, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'SELECT')),
    (@audit, N'UPDATE', N'llm_views.AgentAuditLog', 0, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'UPDATE')),
    (@audit, N'DELETE', N'llm_views.AgentAuditLog', 0, HAS_PERMS_BY_NAME(N'llm_views.AgentAuditLog', N'OBJECT', N'DELETE')),
    (@audit, N'SELECT', N'llm_views.Active_FLEET',  0, HAS_PERMS_BY_NAME(N'llm_views.Active_FLEET', N'OBJECT', N'SELECT')),
    (@audit, N'SELECT', N'dbo.RAW_DATA',            0, HAS_PERMS_BY_NAME(N'dbo.RAW_DATA', N'OBJECT', N'SELECT'));
REVERT;

INSERT @checks VALUES (N'llm', N'LOGIN', N'old shared login (must not exist)', 0, CASE WHEN SUSER_ID(N'llm') IS NULL THEN 0 ELSE 1 END);

SELECT CASE WHEN Expected = Actual THEN 'PASS' ELSE 'FAIL' END AS Result,
       Login,
       CASE WHEN Expected = 1 THEN 'allowed' ELSE 'denied' END AS ShouldBe,
       Permission, ObjectName
FROM @checks
ORDER BY Result, Login;
GO

SET NOEXEC OFF;
EXEC sp_set_session_context N'agent_password', NULL;
EXEC sp_set_session_context N'audit_password', NULL;
GO
