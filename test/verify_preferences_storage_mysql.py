"""在隔离 MySQL 实测 ManagePreferences 的归属、提交及回滚。"""

import argparse
import sys
from pathlib import Path
from typing import cast

from sqlalchemy import event
from sqlalchemy.pool import QueuePool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_config
from implementations.refund.preferences import MySQLPreferences
from interfaces.refund.preference import ManagePreferences
from model.refund.preference import (
    DeletePreference, ListPreferences, PreferenceAction, PreferenceCommand, PreferenceKey,
    PreferenceResult, PreferenceStatus, ResponseDetail, SetPreference,
)
from order_mysql_fixture import isolated_order_database
from utils.database import create_mysql_engine


def verify(config_path, observer):
    engine = create_mysql_engine(load_config(config_path).mysql)
    owner_id = "prefs'_%_owner"
    other_id = "prefs_other"
    key = PreferenceKey.RESPONSE_DETAIL
    read = ListPreferences(action=PreferenceAction.LIST)
    brief = SetPreference(action=PreferenceAction.SET, key=key, value=ResponseDetail.BRIEF)
    detailed = SetPreference(action=PreferenceAction.SET, key=key, value=ResponseDetail.DETAILED)
    delete = DeletePreference(action=PreferenceAction.DELETE, key=key)
    select_value = "SELECT value FROM consumer_preferences WHERE consumer_id=%s AND preference_key=%s"

    def persisted(consumer_id):
        observer.execute(select_value, (consumer_id, key.value))
        row = observer.fetchone()
        return row[0] if row is not None else None

    def call(service: ManagePreferences, command: PreferenceCommand, status: PreferenceStatus,
             detail: ResponseDetail | None = None):
        assert service(command) == PreferenceResult(status=status, detail=detail)
        assert cast(QueuePool, engine.pool).checkedout() == 0

    try:
        owner = MySQLPreferences(engine, consumer_id=owner_id)
        other = MySQLPreferences(engine, consumer_id=other_id)
        observer.execute("INSERT INTO consumer_preferences VALUES (%s, %s, %s, UTC_TIMESTAMP(6))",
                         (other_id, key.value, "detailed"))
        call(owner, read, PreferenceStatus.LISTED)
        call(other, read, PreferenceStatus.LISTED, ResponseDetail.DETAILED)
        call(owner, brief, PreferenceStatus.UPDATED, ResponseDetail.BRIEF)
        assert persisted(owner_id) == "brief" and persisted(other_id) == "detailed"

        pending_values = []

        def fail_before_commit(connection):
            assert connection.in_transaction()
            pending_values.append(connection.exec_driver_sql(select_value, (owner_id, key.value)).scalar_one_or_none())
            assert persisted(owner_id) == "brief"
            connection.exec_driver_sql("SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='isolated failure before commit'")

        event.listen(engine, "commit", fail_before_commit)
        try:
            call(owner, detailed, PreferenceStatus.FAILED)
            assert persisted(owner_id) == "brief"
            call(owner, delete, PreferenceStatus.FAILED)
            assert persisted(owner_id) == "brief"
        finally:
            event.remove(engine, "commit", fail_before_commit)
        assert pending_values == ["detailed", None]

        call(owner, detailed, PreferenceStatus.UPDATED, ResponseDetail.DETAILED)
        assert persisted(owner_id) == "detailed"
        call(other, brief, PreferenceStatus.UPDATED, ResponseDetail.BRIEF)
        call(owner, delete, PreferenceStatus.DELETED)
        call(owner, delete, PreferenceStatus.DELETED)
        call(owner, read, PreferenceStatus.LISTED)
        assert persisted(owner_id) is None and persisted(other_id) == "brief"

        observer.execute("UPDATE consumer_preferences SET value=%s WHERE consumer_id=%s AND preference_key=%s",
                         ("legacy-invalid-value", other_id, key.value))
        call(other, read, PreferenceStatus.FAILED)
        call(owner, read, PreferenceStatus.LISTED)
        call(other, delete, PreferenceStatus.DELETED)
        assert persisted(other_id) is None
        print("通过：双消费者隔离、返回前提交和归还连接、更新与删除在提交前报错后回滚、旧数据校验。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    with isolated_order_database(parser.parse_args().config) as (config_path, observer):
        verify(config_path, observer)
