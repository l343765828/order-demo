import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

from implementations.refund.demo_data import MySQLSeedService


@pytest.fixture
def demo_database():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("UTC_TIMESTAMP", 1, lambda _: "2026-11-26 00:00:00")

    with engine.begin() as connection:
        schema = (
            "CREATE TABLE orders (order_id PRIMARY KEY, consumer_id, payment_status, shipping_status, "
            "paid_amount_fen, created_at, updated_at)",
            "CREATE TABLE refunds (refund_id PRIMARY KEY, order_id REFERENCES orders(order_id), "
            "idempotency_key, amount_fen, source, created_at)",
            "CREATE TABLE conversation_sessions (session_id PRIMARY KEY)",
            "CREATE TABLE conversation_messages (session_id PRIMARY KEY REFERENCES conversation_sessions)",
            "CREATE TABLE conversation_summaries (session_id PRIMARY KEY REFERENCES conversation_messages)",
            "CREATE TABLE refund_dispatches (order_id REFERENCES orders, session_id REFERENCES conversation_messages, "
            "refund_id REFERENCES refunds)",
            "CREATE TABLE consumer_preferences (consumer_id PRIMARY KEY)",
            "CREATE TABLE checkpoint_writes (thread_id)",
            "CREATE TABLE checkpoint_blobs (thread_id)",
            "CREATE TABLE checkpoints (thread_id)",
            "CREATE TABLE checkpoint_migrations (version PRIMARY KEY)",
            "CREATE TABLE policy_catalog_head (revision PRIMARY KEY)",
            "CREATE TABLE policy_releases (version PRIMARY KEY)",
            "CREATE TABLE other_demo (item PRIMARY KEY)",
        )
        for statement in schema:
            connection.exec_driver_sql(statement)
        data = (
            "INSERT INTO orders (order_id) VALUES ('previous-order')",
            "INSERT INTO refunds (refund_id, order_id) VALUES ('previous-refund', 'previous-order')",
            "INSERT INTO conversation_sessions VALUES ('previous-session')",
            "INSERT INTO conversation_messages VALUES ('previous-session')",
            "INSERT INTO conversation_summaries VALUES ('previous-session')",
            "INSERT INTO refund_dispatches VALUES ('previous-order', 'previous-session', 'previous-refund')",
            "INSERT INTO consumer_preferences VALUES ('previous-user')",
            "INSERT INTO checkpoint_writes VALUES ('previous-session')",
            "INSERT INTO checkpoint_blobs VALUES ('orphan-checkpoint')",
            "INSERT INTO checkpoints VALUES ('previous-session')",
            "INSERT INTO checkpoint_migrations VALUES (24)",
            "INSERT INTO policy_catalog_head VALUES (7)",
            "INSERT INTO policy_releases VALUES ('1.0')",
            "INSERT INTO other_demo VALUES ('sample')",
        )
        for statement in data:
            connection.exec_driver_sql(statement)
    try:
        yield engine
    finally:
        engine.dispose()


def test_reset_clears_refund_demo_and_reseeds(demo_database):
    service = MySQLSeedService(demo_database)
    for _ in range(2):
        service.reset()
        with demo_database.connect() as connection:
            assert connection.exec_driver_sql("SELECT order_id FROM orders ORDER BY order_id").scalars().all() == [
                "DEMO-1001", "DEMO-1002", "DEMO-1003", "DEMO-1004", "DEMO-2001",
            ]
            assert connection.exec_driver_sql("SELECT refund_id, amount_fen FROM refunds").all() == [
                ("MOCK-RF-1003", 8800),
            ]
            for table in ("conversation_sessions", "conversation_messages", "conversation_summaries", "refund_dispatches",
                          "consumer_preferences", "checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                assert connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT * FROM policy_catalog_head").scalar_one() == 7
            assert connection.exec_driver_sql("SELECT * FROM policy_releases").scalar_one() == "1.0"
            assert connection.exec_driver_sql("SELECT * FROM checkpoint_migrations").scalar_one() == 24
            assert connection.exec_driver_sql("SELECT * FROM other_demo").scalar_one() == "sample"


def test_reset_rolls_back_deletes_if_seed_refund_fails(demo_database):
    with demo_database.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TRIGGER fail_refund BEFORE INSERT ON refunds
            BEGIN SELECT RAISE(ABORT, 'seed refund failed'); END
        """)
    with pytest.raises(IntegrityError, match="seed refund failed"):
        MySQLSeedService(demo_database).reset()
    with demo_database.connect() as connection:
        assert connection.exec_driver_sql("SELECT order_id FROM orders").scalar_one() == "previous-order"
        assert connection.exec_driver_sql("SELECT refund_id FROM refunds").scalar_one() == "previous-refund"
        assert connection.exec_driver_sql("SELECT session_id FROM refund_dispatches").scalar_one() == "previous-session"
        assert connection.exec_driver_sql("SELECT thread_id FROM checkpoint_blobs").scalar_one() == "orphan-checkpoint"


def test_reset_works_before_checkpoint_initialization(demo_database):
    with demo_database.begin() as connection:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            connection.exec_driver_sql(f"DROP TABLE {table}")
    MySQLSeedService(demo_database).reset()
    with demo_database.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM orders").scalar_one() == 5
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM conversation_sessions").scalar_one() == 0
