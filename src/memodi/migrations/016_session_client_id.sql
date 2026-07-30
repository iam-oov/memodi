-- client_session_id: the Claude Code session id from the SessionStart/
-- SessionEnd hook payload. The /hooks/session-close route matches on this
-- exact value instead of "the workspace's active session", so one window
-- exiting can never close a session another window is still using.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS client_session_id TEXT;

CREATE INDEX IF NOT EXISTS idx_sessions_client_session_id
    ON sessions(client_session_id)
    WHERE client_session_id IS NOT NULL;
