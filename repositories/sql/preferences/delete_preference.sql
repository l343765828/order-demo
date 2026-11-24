DELETE FROM {preferences}
WHERE consumer_id = :consumer_id AND preference_key = :preference_key
