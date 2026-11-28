CREATE TABLE IF NOT EXISTS orders (
 order_id VARCHAR(64) PRIMARY KEY, consumer_id VARCHAR(64) NOT NULL,
 payment_status VARCHAR(24) NOT NULL, shipping_status VARCHAR(24) NOT NULL,
 paid_amount_fen BIGINT UNSIGNED NOT NULL,
 created_at DATETIME(6) NOT NULL, updated_at DATETIME(6) NOT NULL,
 KEY idx_orders_consumer (consumer_id, order_id),
 CONSTRAINT chk_order_payment CHECK ((payment_status='unpaid' AND paid_amount_fen=0) OR (payment_status='paid' AND paid_amount_fen>0)),
 CONSTRAINT chk_order_shipping CHECK (shipping_status IN ('unshipped','shipped'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS conversation_sessions (
 session_id VARCHAR(64) PRIMARY KEY, consumer_id VARCHAR(64) NOT NULL,
 created_at DATETIME(6) NOT NULL, updated_at DATETIME(6) NOT NULL,
 KEY idx_session_consumer (consumer_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS conversation_messages (
 session_id VARCHAR(64) NOT NULL, sequence BIGINT UNSIGNED NOT NULL,
 role VARCHAR(24) NOT NULL, content LONGTEXT NOT NULL, created_at DATETIME(6) NOT NULL,
 PRIMARY KEY (session_id, sequence),
 CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id),
 CONSTRAINT chk_message_role CHECK (role IN ('user','assistant'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS conversation_summaries (
 session_id VARCHAR(64) NOT NULL, revision INT UNSIGNED NOT NULL,
 covered_through_sequence BIGINT UNSIGNED NOT NULL, summary_text LONGTEXT NOT NULL,
 generation_event_id VARCHAR(64) NOT NULL, created_at DATETIME(6) NOT NULL,
 PRIMARY KEY (session_id, revision),
 CONSTRAINT fk_summary_message FOREIGN KEY (session_id, covered_through_sequence) REFERENCES conversation_messages(session_id, sequence)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS consumer_preferences (
 consumer_id VARCHAR(64) NOT NULL, preference_key VARCHAR(64) NOT NULL,
 value VARCHAR(255) NOT NULL, updated_at DATETIME(6) NOT NULL,
 PRIMARY KEY (consumer_id, preference_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS refunds (
 refund_id VARCHAR(64) PRIMARY KEY, order_id VARCHAR(64) NOT NULL,
 idempotency_key VARCHAR(64) NULL, amount_fen BIGINT UNSIGNED NOT NULL,
 source VARCHAR(24) NOT NULL, created_at DATETIME(6) NOT NULL,
 UNIQUE KEY uq_refund_order (order_id), UNIQUE KEY uq_refund_key (idempotency_key),
 UNIQUE KEY uq_refund_id_order (refund_id, order_id),
 CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
 CONSTRAINT chk_refund_source CHECK ((source='seed' AND idempotency_key IS NULL) OR (source='execution' AND idempotency_key IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS refund_dispatches (
 idempotency_key VARCHAR(64) PRIMARY KEY, order_id VARCHAR(64) NOT NULL,
 session_id VARCHAR(64) NOT NULL, source_message_sequence BIGINT UNSIGNED NOT NULL,
 revoked_at DATETIME(6) NULL, status VARCHAR(24) NOT NULL, refund_id VARCHAR(64) NULL,
 trace_id VARCHAR(64) NOT NULL, error_code VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL, updated_at DATETIME(6) NOT NULL,
 blocking_order_id VARCHAR(64) GENERATED ALWAYS AS (CASE WHEN status IN ('pending','unconfirmed') THEN order_id ELSE NULL END) STORED,
 UNIQUE KEY uq_blocking_order (blocking_order_id), UNIQUE KEY uq_dispatch_order (idempotency_key, order_id),
 KEY idx_dispatch_order_status (order_id, status),
 CONSTRAINT fk_dispatch_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
 CONSTRAINT fk_dispatch_message FOREIGN KEY (session_id, source_message_sequence) REFERENCES conversation_messages(session_id, sequence),
 CONSTRAINT fk_dispatch_refund FOREIGN KEY (refund_id, order_id) REFERENCES refunds(refund_id, order_id),
 CONSTRAINT chk_dispatch_status CHECK (status IN ('pending','unconfirmed','succeeded','closed_no_refund'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
