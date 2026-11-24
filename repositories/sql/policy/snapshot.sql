WITH requested AS (
    SELECT COALESCE(CAST(:revision AS UNSIGNED), revision) AS revision
    FROM {head} WHERE singleton = TRUE
), versions AS (
    SELECT document_id, version,
           ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY activated_revision DESC) AS position
    FROM {releases} JOIN requested ON activated_revision <= requested.revision
)
SELECT document_id, version, requested.revision FROM versions CROSS JOIN requested
WHERE position = 1 AND EXISTS (
    SELECT 1 FROM {releases} WHERE activated_revision = requested.revision
) ORDER BY document_id
