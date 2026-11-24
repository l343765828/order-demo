INSERT INTO {releases}
    (document_id, version, source_digest, publisher, qualification, write_state, updated_at)
VALUES (:document_id, :version, :source_digest, :publisher, :qualification, :write_state, UTC_TIMESTAMP(6)) AS attempt
ON DUPLICATE KEY UPDATE source_digest = attempt.source_digest,
    publisher = attempt.publisher, qualification = attempt.qualification,
    write_state = attempt.write_state, updated_at = attempt.updated_at
