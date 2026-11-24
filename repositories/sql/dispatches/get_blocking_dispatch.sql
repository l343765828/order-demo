SELECT d.idempotency_key
FROM {dispatches} d
JOIN {sessions} s USING (session_id)
WHERE d.blocking_order_id = :order_id AND s.consumer_id = :consumer_id
