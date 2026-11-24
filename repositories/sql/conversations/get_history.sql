SELECT session_id, sequence, role, content, created_at
FROM {messages}
WHERE session_id = :session_id
ORDER BY sequence
