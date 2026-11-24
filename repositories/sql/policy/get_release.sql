SELECT document_id, version, source_digest, publisher, qualification,
       write_state, activated_revision, updated_at
FROM {releases} WHERE document_id = :document_id AND version = :version
