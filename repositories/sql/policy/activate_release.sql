UPDATE {releases} SET activated_revision = :revision, updated_at = UTC_TIMESTAMP(6)
WHERE document_id = :document_id AND version = :version AND activated_revision IS NULL
  AND write_state <> :pending_state AND qualification = :qualification
