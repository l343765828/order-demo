UPDATE {releases} SET write_state = :write_state, updated_at = UTC_TIMESTAMP(6)
WHERE document_id = :document_id AND version = :version
