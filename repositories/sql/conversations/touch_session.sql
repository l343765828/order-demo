UPDATE {sessions}
SET updated_at = UTC_TIMESTAMP(6)
WHERE session_id = :session_id
