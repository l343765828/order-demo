"""使用独立进程启动本票政策 MCP 验收服务。"""
import sys
from pathlib import Path
import time
from unittest.mock import patch
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_service.server import open_app
from config import load_config, use_config


def empty_recall(backend, vector, mode, versions):
    search = backend.client.search

    def search_without_matching_keys(*args, filter, **kwargs):
        # Retain the real Milvus round trip while forcing an empty first result.
        return search(*args, filter=f'({filter}) and chunk_id == ""', **kwargs)

    with patch.object(backend.client, 'search', search_without_matching_keys):
        return _recall(backend, vector, mode, versions)

if __name__ == '__main__':
    use_config(sys.argv[2])
    from implementations.policy.backend import PolicyBackend
    from mcp_service.policy import search
    from model.policy.status import PolicySearchMode

    _recall = PolicyBackend.recall
    if '--empty-first' in sys.argv[3:]:
        def recall_with_empty_consultation(self, vector, mode, versions):
            if mode is not PolicySearchMode.CONSULTATION:
                return _recall(self, vector, mode, versions)
            return empty_recall(self, vector, mode, versions)
        PolicyBackend.recall = recall_with_empty_consultation
    if '--pause-supplement' in sys.argv[3:]:
        folder = Path(sys.argv[sys.argv.index('--pause-supplement') + 1])
        trigger = sys.argv[sys.argv.index('--supplement-query') + 1] if '--supplement-query' in sys.argv else '教学印章'
        original_search = search.search_policy

        def pause_supplement(query, mode, backend, **options):
            if trigger in query and mode is PolicySearchMode.CONSULTATION and not (folder / 'armed').exists():
                (folder / 'armed').touch()
                with patch.object(backend, 'recall', lambda *args: empty_recall(backend, *args)):
                    return original_search(query, mode, backend, **options)
            if mode is PolicySearchMode.FORMAL and (folder / 'armed').exists() and not (folder / 'resume').exists():
                pending = folder / 'pending'
                pending.write_text(str(options['catalog_revision']), encoding='ascii')
                pending.replace(folder / 'before-supplement')
                deadline = time.monotonic() + 55
                while not (folder / 'resume').exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('publisher did not release the supplement')
                    time.sleep(.05)
            return original_search(query, mode, backend, **options)

        search.search_policy = pause_supplement
    settings = load_config()
    with open_app(settings) as app:
        uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), access_log=False, log_level='warning')
