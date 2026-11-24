INSERT INTO {dispatches}
    (idempotency_key, order_id, session_id, source_message_sequence, status, trace_id, created_at, updated_at)
SELECT :idempotency_key, :order_id, s.session_id, m.sequence, :status, :trace_id, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
FROM {sessions} s
JOIN {messages} m USING (session_id)
WHERE s.session_id = :session_id AND s.consumer_id = :consumer_id
    AND m.sequence = :source_message_sequence AND m.role = :role
