SELECT consumer_id, preference_key, value, updated_at
FROM {preferences}
WHERE consumer_id = :consumer_id AND preference_key = :preference_key
