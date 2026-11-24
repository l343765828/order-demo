SELECT CAST(COALESCE(MAX(sequence), 0) AS UNSIGNED) AS last_sequence
FROM {messages}
WHERE session_id = :session_id
