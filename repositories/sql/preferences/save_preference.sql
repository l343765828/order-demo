INSERT INTO {preferences} (consumer_id, preference_key, value, updated_at)
VALUES (:consumer_id, :preference_key, :value, UTC_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE value = :value, updated_at = UTC_TIMESTAMP(6)
