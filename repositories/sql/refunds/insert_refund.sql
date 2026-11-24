INSERT INTO {refunds} (refund_id, order_id, idempotency_key, amount_fen, currency, source, created_at)
VALUES (:refund_id, :order_id, :idempotency_key, :amount_fen, :currency, :source, UTC_TIMESTAMP(6))
