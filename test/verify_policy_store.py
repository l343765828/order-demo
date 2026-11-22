"""核验已发布政策、历史版本隔离及真实召回和重排。"""
import math
import hashlib
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from implementations.policy.source import parse_policy
from mcp_service.policy.backend import PolicyBackend
from config import use_config
from config.limits import PolicyLimits
from model.policy.search import PolicyEntry
from model.policy.status import PolicySearchMode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', help='JSON 配置文件路径')
    use_config(parser.parse_args().config)
    backend = PolicyBackend()
    try:
        client = backend.client
        backend.create_collection()
        rows = client.query(PolicyLimits.MILVUS_COLLECTION_NAME, filter='document_id == "DEMO-REFUND-POLICY" and version == "1.0"',
                            output_fields=['chunk_id','document_id','title','version','clause_id','entry_type','text','embedding'], limit=100, consistency_level='Strong')
        assert sum(row['entry_type'] == 'policy' for row in rows) == 14
        assert sum(row['entry_type'] == 'faq' for row in rows) == 7
        source = Path(__file__).resolve().parents[1] / 'policy' / 'refund-policy.md'
        entries = parse_policy(source.read_text(encoding='utf-8'))
        expected = {entry.chunk_id: entry.model_dump() for entry in entries}
        assert all({k: row[k] for k in expected[row['chunk_id']]} == expected[row['chunk_id']] for row in rows)
        assert all(len(row['embedding']) == 1024 and all(math.isfinite(v) for v in row['embedding'])
                   and abs(math.sqrt(sum(v*v for v in row['embedding'])) - 1) < 0.0001 for row in rows)
        query = '付款后尚未发货可以申请退款吗'
        vector = backend.embed(query)
        assert len(vector) == 1024 and abs(math.sqrt(sum(v*v for v in vector)) - 1) < 0.0001
        old = dict(next(row for row in rows if row['clause_id'] == 'FAQ-001')); old['text'] = '测试问题？\n测试答案。'; old['version'] = '0.0-probe'; old['chunk_id'] = hashlib.sha256(f'DEMO-REFUND-POLICY:0.0-probe:{old["clause_id"]}'.encode()).hexdigest()
        old.pop('embedding')
        backend.publish([PolicyEntry.model_validate(old)])
        assert client.query(PolicyLimits.MILVUS_COLLECTION_NAME, filter=f'chunk_id == "{old["chunk_id"]}"',
                            output_fields=['version'], consistency_level='Strong')
        print('current counts: policy=14 faq=7; stored vectors and source exact; retained history id=', old['chunk_id'])
        for mode in (PolicySearchMode.CONSULTATION, PolicySearchMode.FORMAL):
            candidates = backend.recall(vector, mode)
            assert len(candidates) <= 8 and all(c.version == '1.0' for c in candidates)
            if mode is PolicySearchMode.FORMAL: assert all(c.entry_type == 'policy' for c in candidates)
            ranked = backend.rerank(query, candidates)
            assert len(ranked) <= 3 and all(c in candidates for c in ranked)
            print(mode, 'recalled=', [c.clause_id for c in candidates], 'ranked=', [c.clause_id for c in ranked])
    finally:
        backend.close()

if __name__ == '__main__': main()
