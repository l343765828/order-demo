INSERT INTO {refunds} (refund_id, order_id, idempotency_key, amount_fen, source, created_at)
VALUES (:refund_id, :order_id, :idempotency_key, :amount_fen, :source, UTC_TIMESTAMP(6))
