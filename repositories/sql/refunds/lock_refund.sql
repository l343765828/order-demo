SELECT refund_id, order_id, idempotency_key, amount_fen, currency, source, created_at
FROM {refunds}
WHERE order_id = :order_id
FOR UPDATE
