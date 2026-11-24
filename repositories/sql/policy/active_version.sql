SELECT document_id, version FROM {releases}
WHERE document_id = :document_id AND activated_revision IS NOT NULL
ORDER BY activated_revision DESC LIMIT 1
