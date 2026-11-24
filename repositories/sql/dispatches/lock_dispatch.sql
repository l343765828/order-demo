SELECT idempotency_key, order_id, session_id, source_message_sequence, revoked_at, status,
    refund_id, trace_id, error_code, created_at, updated_at, blocking_order_id
FROM {dispatches}
WHERE idempotency_key = :idempotency_key
FOR UPDATE
