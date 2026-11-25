SELECT refund_id, order_id, idempotency_key, amount_fen, source, created_at
FROM {refunds}
WHERE order_id = :order_id
FOR UPDATE
