INSERT INTO {orders} (order_id, consumer_id, payment_status, shipping_status, paid_amount_fen, currency, created_at, updated_at)
VALUES (:order_id, :consumer_id, :payment_status, :shipping_status, :paid_amount_fen, :currency, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
