-- 按订单编号和消费者归属查询订单事实，以关联退款记录判断是否已退款。
SELECT o.order_id, o.consumer_id, o.payment_status, o.shipping_status,
       o.paid_amount_fen, o.currency, o.created_at, o.updated_at, r.refund_id
FROM `{orders}` AS o
LEFT JOIN `{refunds}` AS r ON r.order_id = o.order_id
WHERE o.order_id = :order_id AND o.consumer_id = :consumer_id
