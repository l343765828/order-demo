-- 退款事务内以当前读取得前面已锁定的订单事实。
SELECT order_id, consumer_id, payment_status, shipping_status, paid_amount_fen, currency, created_at, updated_at
FROM {orders}
WHERE order_id = :order_id AND consumer_id = :consumer_id
FOR UPDATE
