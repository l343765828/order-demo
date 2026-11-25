SELECT r.refund_id, r.order_id, r.idempotency_key, r.amount_fen, r.source, r.created_at
FROM {refunds} r
JOIN {orders} o USING (order_id)
WHERE r.order_id = :order_id AND o.consumer_id = :consumer_id
