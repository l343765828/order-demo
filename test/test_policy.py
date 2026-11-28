import json
import sys
import unittest
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.limits import PolicyLimits
from implementations.policy.source import parse_policy, read_policy_document
from model.policy.catalog import PolicySnapshot, PolicyVersion
from model.policy.status import PolicySearchMode, PolicySearchStatus
from utils.vectors import normalize_vector

POLICY_SOURCE = Path(__file__).resolve().parents[1] / 'policy' / 'refund-policy.md'


def test_cutover_policy_changes_only_rf_013_and_keeps_business_outcome():
    old = read_policy_document(POLICY_SOURCE.read_text(encoding='utf-8'), version='1.0')
    new = read_policy_document(POLICY_SOURCE.with_name('refund-policy-1.1.md').read_text(encoding='utf-8'), version='1.1')
    assert new.target.document_id == old.target.document_id
    old_texts = {entry.clause_id: entry.text for entry in old.entries}
    new_texts = {entry.clause_id: entry.text for entry in new.entries}
    assert new_texts.keys() == old_texts.keys()
    assert [clause for clause in new_texts if new_texts[clause] != old_texts[clause]] == ['RF-013']
    assert '办理无需预先取得政策依据' in new_texts['RF-013']
    assert '保留已确认的业务结果' in new_texts['RF-013']


class PolicyTests(unittest.TestCase):
    def test_source_entries_and_faq_without_citation(self):
        source = POLICY_SOURCE.read_text(encoding='utf-8')
        entries = parse_policy(source)
        self.assertEqual(sum(e.entry_type == 'policy' for e in entries), 14)
        self.assertEqual(sum(e.entry_type == 'faq' for e in entries), 7)
        faq = '## FAQ-001 — 问题？\n\n只有答案。\n'
        self.assertEqual(parse_policy(faq)[0].text, '问题？\n只有答案。')

    def test_vector_contract(self):
        vector = normalize_vector([3.0, 4.0], expected_dimensions=2)
        self.assertEqual(vector, [0.6, 0.8])
        for bad in ([0.0, 0.0], [float('nan'), 0.0], [1.0]):
            with self.assertRaises(ValueError):
                normalize_vector(bad, expected_dimensions=2)

    def test_entry_rejects_excess_utf8_bytes(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            parse_policy('## RF-001 — 标题\n\n' + '汉' * 1366)

    def test_entry_rejects_invalid_chunk_id(self):
        from pydantic import ValidationError
        entry = parse_policy('## RF-001 — 标题\n\n正文')[0].model_dump()
        from model.policy.search import PolicyEntry
        for invalid in ('', 'not-sha256', 'a' * 64):
            with self.assertRaises(ValidationError):
                PolicyEntry.model_validate({**entry, 'chunk_id': invalid})

    def test_stored_vector_must_be_finite_and_normalized(self):
        from model.policy.search import PolicyVectorRecord
        from pydantic import ValidationError
        entry = parse_policy('## RF-001 — 标题\n\n正文')[0]
        for bad in ([0.0] * 1024, [float('nan')] * 1024, [1.0]):
            with self.assertRaises(ValidationError):
                PolicyVectorRecord(**entry.model_dump(), embedding=bad)


class SearchTests(unittest.TestCase):
    def test_search_failure_never_returns_unranked_candidates(self):
        from mcp_service.policy.search import search_policy
        candidate = parse_policy('## RF-001 — 标题\n\n正文')[0]

        class FailedBackend:
            def embed(self, text): return [1.0] + [0.0] * 1023
            def recall(self, vector, mode, versions): return [candidate]
            def rerank(self, query, candidates): raise TimeoutError()
        class Catalog:
            def snapshot(self, revision):
                return PolicySnapshot(revision=1, versions=[PolicyVersion(document_id=candidate.document_id, version=candidate.version)])
        result = search_policy('退款', PolicySearchMode.FORMAL, FailedBackend(), catalog=Catalog())
        self.assertIs(result.status, PolicySearchStatus.FAILED)
        self.assertEqual(result.candidates, [])

    def test_milvus_recall_returns_typed_entries_without_vectors(self):
        from implementations.policy.backend import PolicyBackend
        from model.policy.search import PolicyEntry

        row = {'chunk_id': parse_policy('## RF-001 — 标题\n\n正文')[0].chunk_id, 'document_id':'DEMO-REFUND-POLICY', 'title':'政策',
               'version':'1.0', 'clause_id':'RF-001', 'entry_type':'policy', 'text':'内容'}
        class Client:
            def search(self, *args, **kwargs):
                return [[{'entity': row}]]
        backend = PolicyBackend.__new__(PolicyBackend)
        backend.client = Client()
        backend.collection = PolicyLimits.MILVUS_COLLECTION_NAME
        version = PolicyVersion(document_id=row['document_id'], version=row['version'])
        entry = backend.recall([1.0] + [0.0] * 1023, PolicySearchMode.FORMAL, [version])[0]
        self.assertIsInstance(entry, PolicyEntry)
        self.assertEqual(entry.clause_id, 'RF-001')

    def test_publish_writes_complete_record(self):
        from implementations.policy.backend import PolicyBackend

        class Client:
            def upsert(self, collection, rows, **kwargs): self.rows = rows
            def flush(self, *args, **kwargs): pass
        backend = PolicyBackend.__new__(PolicyBackend)
        backend.client = Client()
        backend.collection = PolicyLimits.MILVUS_COLLECTION_NAME
        backend.embed = lambda text: [1.0] + [0.0] * 1023
        entry = parse_policy('## RF-001 — 标题\n\n正文')[0]
        backend.publish([entry])
        self.assertEqual(backend.client.rows[0]['clause_id'], 'RF-001')
        self.assertEqual(backend.client.rows[0]['text'], '标题\n正文')
        self.assertEqual(len(backend.client.rows[0]['embedding']), 1024)


@pytest.mark.parametrize('scenario', ['ranked', 'empty', 'bad_vector', 'bad_indexes', 'http_error'])
def test_policy_model_requests_and_fallback(monkeypatch, scenario):
    from config import AppConfig, EmbeddingConfig, MCPConfig, MySQLConfig, PolicyConfig, QwenConfig, RerankerConfig
    from implementations.policy import backend
    from mcp_service.policy.search import search_policy

    entries = parse_policy('## RF-001 — 标题一\n\n正文一\n\n## RF-002 — 标题二\n\n正文二')
    settings = AppConfig(
        mysql=MySQLConfig('db', 3306, 'demo', 'sample'),
        mcp=MCPConfig('http://mcp.test/mcp', 8765, 'x' * 32),
        qwen=QwenConfig(base_url='http://qwen.test/v1', model='chat', timeout=10, max_completion_tokens=64),
        policy=PolicyConfig(milvus_uri='http://milvus.test', milvus_token='sample'),
        embedding=EmbeddingConfig(url='http://embed.test:9010/custom/encode?api=1', model='encoder', timeout=7),
        reranker=RerankerConfig(url='https://rank.test:9020/custom/sort', model='ranker', timeout=13),
    )

    class Milvus:
        def search(self, *args, **kwargs):
            return [[] if scenario == 'empty' else [{'entity': entry.model_dump()} for entry in entries]]

    class Catalog:
        def snapshot(self, revision):
            return PolicySnapshot(revision=1, versions=[PolicyVersion(
                document_id=entries[0].document_id, version=entries[0].version,
            )])

    calls = []
    real_client = httpx.Client

    def client(*, timeout, trust_env):
        assert not trust_env

        def respond(request):
            body = json.loads(request.content)
            calls.append((str(request.url), body, request.extensions['timeout']['read']))
            if request.url.host == 'embed.test':
                vector = [1.0] if scenario == 'bad_vector' else [1.0] + [0.0] * 1023
                return httpx.Response(200, json={'data': [{'embedding': vector}]})
            if scenario == 'http_error':
                return httpx.Response(503)
            indexes = [1, 2] if scenario == 'bad_indexes' else [1, 0]
            return httpx.Response(200, json={'results': [{'index': index} for index in indexes]})

        return real_client(timeout=timeout, trust_env=trust_env, transport=httpx.MockTransport(respond))

    def milvus_client(*, uri, token, timeout):
        assert (uri, token, timeout) == ('http://milvus.test', 'sample', PolicyLimits.MILVUS_CONNECT_TIMEOUT_SECONDS)
        return Milvus()

    monkeypatch.setattr(backend, 'MilvusClient', milvus_client)
    monkeypatch.setattr(backend.httpx, 'Client', client)
    result = search_policy('退款', PolicySearchMode.FORMAL, backend.PolicyBackend(settings), catalog=Catalog())
    expected = [('http://embed.test:9010/custom/encode?api=1', {'model': 'encoder', 'input': ['退款']}, 7)]
    if scenario in {'ranked', 'bad_indexes', 'http_error'}:
        expected.append(('https://rank.test:9020/custom/sort',
                         {'model': 'ranker', 'query': '退款', 'documents': ['标题一\n正文一', '标题二\n正文二']}, 13))
    assert calls == expected
    assert [item.clause_id for item in result.candidates] == (['RF-002', 'RF-001'] if scenario == 'ranked' else [])
    assert result.status is (PolicySearchStatus.OK if scenario in {'ranked', 'empty'} else PolicySearchStatus.FAILED)


class PolicyRequestTests(unittest.TestCase):
    def test_request_rejects_backend_settings_and_unknown_mode(self):
        from model.policy.search import SearchPolicyRequest
        from pydantic import ValidationError
        for args in ({'query':'退款', 'mode':'all', 'collection':'other'}, {'query':'退款', 'mode':'all'}):
            with self.assertRaises(ValidationError): SearchPolicyRequest.model_validate(args)

class ResultTests(unittest.TestCase):
    def test_rejects_failed_results_with_candidates_and_extra_fields(self):
        from model.policy.search import PolicySearchResult
        from pydantic import ValidationError
        candidate = {'document_id':'d','title':'t','version':'1','clause_id':'RF-001','entry_type':'policy','text':'x'}
        for value in ({'status':'failed','event_id':'event','catalog_revision':None,'candidates':[candidate]},
                      {'status':'ok','event_id':'event','catalog_revision':1,'candidates':[], 'extra':1}):
            with self.assertRaises(ValidationError): PolicySearchResult.model_validate(value)

    def test_rejects_source_header_mismatch(self):
        source = POLICY_SOURCE.read_text(encoding='utf-8')
        with self.assertRaises(ValueError): read_policy_document(source.replace('版本：1.0', '版本：2.0'), version='1.0')

class HostScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_tool_cannot_override_formal_scope(self):
        from implementations.policy.tool import make_policy_tool
        from model.policy.search import SearchPolicyRequest, PolicySearchResult
        from model.policy.status import PolicySearchMode
        seen = []
        class Bound:
            async def call(self, args, **kwargs):
                seen.append(args)
                return PolicySearchResult(status=PolicySearchStatus.OK, event_id='event',
                                          candidates=[], catalog_revision=1)
        tool = make_policy_tool(Bound(), 'formal')
        self.assertNotIn('mode', tool.args)
        result = await tool.ainvoke({'query':'退款'})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(seen, [SearchPolicyRequest(query='退款', mode=PolicySearchMode.FORMAL)])
        with self.assertRaises(Exception):
            await tool.ainvoke({'query':'退款', 'mode':'consultation'})
