"""使用真实 MySQL 和独立 MCP 复验无需模型或向量服务的业务路径。"""

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_config
from implementations.policy.catalog import MySQLPolicyCatalog, initialize_policy_catalog
from model.policy.catalog import (
    PolicyPublicationApproval,
    PolicyVersion,
    PolicyWriteState,
    QualificationConfirmation,
)
from order_mysql_fixture import isolated_order_database, order_mcp_process
from utils.database import create_mysql_engine
from verify_policy_catalog import check_mysql_boundaries
from verify_policy_mcp import verify
from verify_refund_submission import check_legacy_order_facts, check_lookup_scope, check_server


def check_policy_storage(mysql, cursor):
    initialize_policy_catalog(mysql.database, mysql)
    engine = create_mysql_engine(mysql)
    evidence = []
    try:
        catalog = MySQLPolicyCatalog(engine)
        for index in (1, 2):
            target = PolicyVersion(document_id=f"LOCAL-PROBE-{index}", version="1.0")
            approval = PolicyPublicationApproval(source_digest="0" * 64, publisher="local-probe",
                                                 qualification=QualificationConfirmation.UNCHANGED)
            with catalog.lock_version(target) as attempt:
                attempt.record_attempt(approval, write_state=PolicyWriteState.PENDING)
                attempt.finish_write()
                assert attempt.activate(None) == index
        assert len(catalog.snapshot(1).versions) == 1
        assert len(catalog.snapshot().versions) == 2
        check_mysql_boundaries(catalog, engine, cursor, mysql.database, evidence)
    finally:
        engine.dispose()
    print("policy storage:", evidence, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    with isolated_order_database(args.config, allow_write=True) as (config, cursor):
        mysql = load_config(config).mysql
        check_policy_storage(mysql, cursor)
        settings = json.loads(config.read_text(encoding="utf-8"))
        settings["policy"] = dict(milvus_uri="http://127.0.0.1:1", milvus_token="local-probe",
                                  catalog_database=mysql.database)
        settings["embedding"] = dict(url="http://127.0.0.1:1/encode", model="probe-embed", timeout=1)
        settings["reranker"] = dict(url="http://127.0.0.1:1/rank", model="probe-rank", timeout=1)
        config.write_text(json.dumps(settings), encoding="utf-8")
        with order_mcp_process(config):
            print("refund:", check_server(config, cursor)["case"], flush=True)
            for check in (check_lookup_scope, check_legacy_order_facts):
                check(config, cursor, lambda result: print("refund:", result["case"], flush=True))
            mcp = load_config(config).mcp
            asyncio.run(verify(mcp.url, mcp.read_token, True))


if __name__ == "__main__":
    main()
