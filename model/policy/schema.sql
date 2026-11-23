CREATE TABLE IF NOT EXISTS {head} (
    singleton BOOLEAN NOT NULL PRIMARY KEY CHECK (singleton = TRUE),
    revision BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;

INSERT IGNORE INTO {head} (singleton, revision) VALUES (TRUE, 0);

CREATE TABLE IF NOT EXISTS {releases} (
    document_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    publisher VARCHAR(128) NOT NULL,
    qualification VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    write_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    activated_revision BIGINT UNSIGNED NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (document_id, version),
    UNIQUE KEY uq_policy_activation (activated_revision),
    CONSTRAINT ck_policy_qualification CHECK (qualification IN ('unchanged', 'changed')),
    CONSTRAINT ck_policy_write CHECK (write_state IN ('not_started', 'pending', 'finished')),
    CONSTRAINT ck_policy_activation CHECK (
        activated_revision IS NULL OR (activated_revision > 0 AND write_state <> 'pending' AND qualification = 'unchanged')
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4;
