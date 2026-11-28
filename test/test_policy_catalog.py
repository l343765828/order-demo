from contextlib import nullcontext
from datetime import datetime
from unittest.mock import Mock

import pytest

from implementations.policy.publish import publish_policy
from implementations.policy.source import SOURCE_PATH, read_policy_document
from mcp_service.policy.search import search_policy
from model.policy.catalog import (
    PolicyRelease, PolicySnapshot, PolicyVersion, PolicyWriteState, PublicationStatus, QualificationConfirmation,
)
from model.policy.status import PolicySearchMode, PolicySearchStatus


def source(document_id='SECOND-POLICY', version='1.2'):
    return (f'# 补充政策\n\n文档编号：{document_id}\n版本：{version}\n\n'
            '## RF-003 — 正式条款\n\n条款正文。\n\n## FAQ-001 — 问题？\n\n独立答案。\n')


def test_source_uses_requested_identity_and_preserves_independent_faq():
    document = read_policy_document(source(), version='1.2')
    assert document.target == PolicyVersion(document_id='SECOND-POLICY', version='1.2')
    assert document.entries[1].text == '问题？\n独立答案。'
    assert read_policy_document(source('THIRD-POLICY'), version='1.2').entries[0].chunk_id != document.entries[0].chunk_id
    with pytest.raises(ValueError):
        read_policy_document(source(), version='1.3')


def test_search_uses_complete_snapshot_and_fails_closed_on_unknown_revision():
    document = read_policy_document(source(), version='1.2')
    other = read_policy_document(source('THIRD-POLICY'), version='1.2')
    snapshot = PolicySnapshot(revision=4, versions=[document.target, other.target])
    catalog = Mock()
    catalog.snapshot.return_value = snapshot
    backend = Mock()
    backend.recall.return_value = [document.entries[0], other.entries[0]]
    backend.rerank.side_effect = lambda query, candidates: candidates
    result = search_policy('退款', PolicySearchMode.FORMAL, backend, catalog=catalog, catalog_revision=4)
    catalog.snapshot.assert_called_once_with(4)
    assert backend.recall.call_args.args[2] == snapshot.versions
    assert result.catalog_revision == 4
    assert [(item.document_id, item.clause_id) for item in result.candidates] == [
        ('SECOND-POLICY', 'RF-003'), ('THIRD-POLICY', 'RF-003')]
    backend.reset_mock()
    catalog.snapshot.side_effect = ValueError('无效修订')
    assert search_policy('退款', PolicySearchMode.FORMAL, backend, catalog=catalog, catalog_revision=5).status is PolicySearchStatus.FAILED
    backend.embed.assert_not_called()


def test_qualification_change_cannot_start_index_write():
    document = read_policy_document(source(), version='1.2')
    catalog, open_backend = Mock(), Mock()
    catalog.lock_version.return_value.__enter__ = Mock()
    catalog.lock_version.return_value.__enter__.return_value.release.return_value = None
    catalog.lock_version.return_value.__exit__ = Mock(return_value=False)
    with pytest.raises(ValueError, match='结构化规则'):
        publish_policy(document, publisher='test', qualification=QualificationConfirmation.CHANGED,
                       catalog=catalog, open_backend=open_backend)
    open_backend.assert_not_called()


def previous_release(document, qualification, *, revision=None, digest=None):
    return PolicyRelease(**document.target.model_dump(), source_digest=digest or document.source_digest,
                         publisher='test', qualification=qualification, write_state=PolicyWriteState.NOT_STARTED,
                         activated_revision=revision, updated_at=datetime(2026, 11, 23))


def test_previously_activated_release_can_be_confirmed_without_vector_service():
    document = read_policy_document(source(), version='1.2')
    attempt, catalog, open_backend = Mock(), Mock(), Mock(side_effect=ConnectionError)
    catalog.lock_version.return_value = nullcontext(attempt)
    attempt.release.return_value = previous_release(document, QualificationConfirmation.UNCHANGED, revision=3)
    result = publish_policy(document, publisher='test', qualification=QualificationConfirmation.UNCHANGED,
                            catalog=catalog, open_backend=open_backend)
    assert result.status is PublicationStatus.ALREADY_ACTIVATED and result.catalog_revision == 3
    open_backend.assert_not_called()


def test_blocked_baseline_cannot_be_reclassified_or_clear_legacy_entries():
    document = read_policy_document(SOURCE_PATH.read_text(encoding='utf-8'), version='1.0')
    attempt, catalog, backend = Mock(), Mock(), Mock()
    catalog.lock_version.return_value = nullcontext(attempt)
    attempt.release.return_value = previous_release(document, QualificationConfirmation.CHANGED)
    open_backend = Mock(return_value=nullcontext(backend))
    with pytest.raises(ValueError, match='原件已声明改变资格'):
        publish_policy(document, publisher='test', qualification=QualificationConfirmation.UNCHANGED,
                       catalog=catalog, open_backend=open_backend)
    open_backend.assert_not_called()

    attempt.release.return_value = previous_release(document, QualificationConfirmation.CHANGED, digest='0' * 64)
    attempt.activate.return_value = 1
    backend.has_entries.return_value = True
    backend.read_version.return_value = document.entries
    backend.recall.return_value = [document.entries[0]]
    backend.rerank.return_value = [document.entries[0]]
    with pytest.raises(ValueError, match='缺少发布记录'):
        publish_policy(document, publisher='test', qualification=QualificationConfirmation.UNCHANGED,
                       catalog=catalog, open_backend=open_backend)
    attempt.activate.assert_not_called()
    backend.clear_version.assert_not_called()
    backend.publish.assert_not_called()
