import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_service.policy import parse_policy, normalize_vector
from model.status import PolicySearchMode, PolicySearchStatus


class PolicyTests(unittest.TestCase):
    def test_source_entries_and_faq_without_citation(self):
        source = Path('.scratch/refund-agent-all-tech-demo/refund-policy.md').read_text(encoding='utf-8')
        entries = parse_policy(source)
        self.assertEqual(sum(e.entry_type == 'policy' for e in entries), 14)
        self.assertEqual(sum(e.entry_type == 'faq' for e in entries), 7)
        faq = '## FAQ-001 — 问题？\n\n只有答案。\n'
        self.assertEqual(parse_policy(faq)[0].text, '问题？\n只有答案。')

    def test_vector_contract(self):
        vector = normalize_vector([3.0] + [0.0] * 1023)
        self.assertAlmostEqual(vector[0], 1.0)
        for bad in ([0.0] * 1024, [float('nan')] * 1024, [1.0]):
            with self.assertRaises(ValueError):
                normalize_vector(bad)

    def test_entry_rejects_excess_utf8_bytes(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            parse_policy('## RF-001 — 标题\n\n' + '汉' * 1366)

    def test_entry_rejects_invalid_chunk_id(self):
        from pydantic import ValidationError
        entry = parse_policy('## RF-001 — 标题\n\n正文')[0].model_dump()
        from model.policy import PolicyEntry
        for invalid in ('', 'not-sha256', 'a' * 64):
            with self.assertRaises(ValidationError):
                PolicyEntry.model_validate({**entry, 'chunk_id': invalid})

    def test_stored_vector_must_be_finite_and_normalized(self):
        from model.policy import PolicyVectorRecord
        from pydantic import ValidationError
        entry = parse_policy('## RF-001 — 标题\n\n正文')[0]
        for bad in ([0.0] * 1024, [float('nan')] * 1024, [1.0]):
            with self.assertRaises(ValidationError):
                PolicyVectorRecord(**entry.model_dump(), embedding=bad)


class SearchTests(unittest.TestCase):
    def test_search_failure_never_returns_unranked_candidates(self):
        from mcp_service.policy import search_policy
        class FailedBackend:
            def embed(self, text): return [1.0] + [0.0] * 1023
            def recall(self, vector, mode): return [{'text': 'candidate'}]
            def rerank(self, query, candidates): raise TimeoutError()
        result = search_policy('退款', PolicySearchMode.FORMAL, FailedBackend())
        self.assertIs(result.status, PolicySearchStatus.FAILED)
        self.assertEqual(result.candidates, [])

    def test_milvus_recall_returns_typed_entries_without_vectors(self):
        from mcp_service.policy_backend import PolicyBackend
        from model.policy import PolicyEntry

        row = {'chunk_id': parse_policy('## RF-001 — 标题\n\n正文')[0].chunk_id, 'document_id':'DEMO-REFUND-POLICY', 'title':'政策',
               'version':'1.0', 'clause_id':'RF-001', 'entry_type':'policy', 'text':'内容'}
        class Client:
            def search(self, *args, **kwargs):
                return [[{'entity': row}]]
        backend = PolicyBackend.__new__(PolicyBackend)
        backend.client = Client()
        entry = backend.recall([1.0] + [0.0] * 1023, PolicySearchMode.FORMAL)[0]
        self.assertIsInstance(entry, PolicyEntry)
        self.assertEqual(entry.clause_id, 'RF-001')

    def test_publish_writes_complete_record(self):
        from mcp_service.policy_backend import PolicyBackend

        class Client:
            def upsert(self, collection, rows, **kwargs): self.rows = rows
            def flush(self, *args, **kwargs): pass
        backend = PolicyBackend.__new__(PolicyBackend)
        backend.client = Client()
        backend.create_collection = lambda: None
        backend.embed = lambda text: [1.0] + [0.0] * 1023
        entry = parse_policy('## RF-001 — 标题\n\n正文')[0]
        backend.publish([entry])
        self.assertEqual(backend.client.rows[0]['clause_id'], 'RF-001')
        self.assertEqual(backend.client.rows[0]['text'], '标题\n正文')
        self.assertEqual(len(backend.client.rows[0]['embedding']), 1024)

class PolicyRequestTests(unittest.TestCase):
    def test_request_rejects_backend_settings_and_unknown_mode(self):
        from model.policy import SearchPolicyRequest
        from pydantic import ValidationError
        for args in ({'query':'退款', 'mode':'all', 'collection':'other'}, {'query':'退款', 'mode':'all'}):
            with self.assertRaises(ValidationError): SearchPolicyRequest.model_validate(args)

class ResultTests(unittest.TestCase):
    def test_rejects_failed_results_with_candidates_and_extra_fields(self):
        from model.policy import PolicySearchResult
        from pydantic import ValidationError
        candidate = {'document_id':'d','title':'t','version':'1','clause_id':'RF-001','entry_type':'policy','text':'x'}
        for value in ({'status':'failed','event_id':'event','candidates':[candidate]},
                      {'status':'ok','event_id':'event','candidates':[], 'extra':1}):
            with self.assertRaises(ValidationError): PolicySearchResult.model_validate(value)

    def test_rejects_source_header_mismatch(self):
        from mcp_service.policy import validate_policy_source
        source = Path('order-demo/policy/refund-policy.md').read_text(encoding='utf-8')
        with self.assertRaises(ValueError): validate_policy_source(source.replace('版本：1.0', '版本：2.0'))

class HostScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_tool_cannot_override_formal_scope(self):
        from utils.policy_tool import make_policy_tool
        from model.policy import PolicySearchResult
        seen = []
        class Bound:
            async def call(self, args, **kwargs):
                seen.append(args)
                return PolicySearchResult(status=PolicySearchStatus.OK, event_id='event', candidates=[])
        tool = make_policy_tool(Bound(), 'formal')
        self.assertNotIn('mode', tool.args)
        result = await tool.ainvoke({'query':'退款'})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(seen[0]['mode'], 'formal')
        with self.assertRaises(Exception):
            await tool.ainvoke({'query':'退款', 'mode':'consultation'})
