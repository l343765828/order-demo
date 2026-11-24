UPDATE {dispatches}
SET status = :status, refund_id = :refund_id, error_code = :error_code, updated_at = UTC_TIMESTAMP(6)
WHERE idempotency_key = :idempotency_key AND status IN (:first_expected_status, :second_expected_status)
