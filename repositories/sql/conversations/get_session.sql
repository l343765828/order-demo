SELECT session_id, consumer_id, created_at, updated_at
FROM {sessions}
WHERE session_id = :session_id
