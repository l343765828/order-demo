SELECT 1
FROM {orders}
WHERE order_id = :order_id AND consumer_id = :consumer_id
FOR UPDATE
