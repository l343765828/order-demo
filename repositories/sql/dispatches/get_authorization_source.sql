SELECT 1
FROM {sessions} s
JOIN {messages} m USING (session_id)
WHERE s.session_id = :session_id AND s.consumer_id = :consumer_id
    AND m.sequence = :source_message_sequence AND m.role = :role
