"""按权威原件发布完整政策版本并切换目录。"""

from collections.abc import Callable
from contextlib import AbstractContextManager

from config.demo import DemoPolicy
from implementations.policy.backend import PolicyBackend
from implementations.policy.catalog import MySQLPolicyCatalog, PublicationConflict
from model.policy.catalog import (
    PolicyDocument, PolicyPublicationApproval, PolicyPublicationResult, PolicyWriteState,
    PublicationStatus, QualificationConfirmation,
)
from model.policy.status import PolicyEntryType, PolicySearchMode


# 强一致回读完整条目，再通过正常召回与重排确认此版本可检索。
def _verify_index(document: PolicyDocument, backend: PolicyBackend) -> None:

    # region 核对全部条目及原文，包括多余或重复的主键
    expected = sorted(document.entries, key=lambda item: item.chunk_id)
    actual = sorted(backend.read_version(document.target), key=lambda item: item.chunk_id)
    if actual != expected:
        raise ValueError('Milvus 政策版本回读与权威原件不一致')
    # endregion

    # region 以原件条目验证咨询检索以及文档适用的正式条款检索
    probes = [(PolicySearchMode.CONSULTATION, document.entries[0])]
    formal_probe = next((entry for entry in document.entries if entry.entry_type is PolicyEntryType.POLICY), None)
    if formal_probe is not None:
        probes.append((PolicySearchMode.FORMAL, formal_probe))
    for mode, probe in probes:
        query = probe.text.split('\n', 1)[0]
        candidates = backend.recall(backend.embed(query), mode, [document.target])
        if not candidates:
            raise ValueError('政策版本未通过检索验证')
        ranked = backend.rerank(query, candidates)
        if not ranked:
            raise ValueError('政策版本未通过重排验证')
    # endregion


# 保存资格确认，发布并切换一个文档版本；同版本执行和历史核对共享互斥锁。
def publish_policy(document: PolicyDocument, *, publisher: str, qualification: QualificationConfirmation,
                   catalog: MySQLPolicyCatalog, open_backend: Callable[[], AbstractContextManager[PolicyBackend]],
                   confirm_write_ended: bool = False) -> PolicyPublicationResult:

    # region 读取发布基准和版本记录，优先核对曾经生效的版本
    approval = PolicyPublicationApproval(source_digest=document.source_digest, publisher=publisher,
                                         qualification=qualification)
    with catalog.lock_version(document.target) as attempt:
        active_version = attempt.active_version()
        previous = attempt.release()
        if (
            # 该文档版本有已持久化的发布记录。
            previous is not None
            # 发布记录证明该版本曾经生效。
            and previous.activated_revision is not None
        ):

            # region 确认为原版本重发时返回历史修订，源或资格确认不同则拒绝重发
            if (
                # 新原件与该版本首次生效时的原件不同。
                previous.source_digest != document.source_digest
                # 本次声明与首次生效时的资格确认不同。
                or previous.qualification is not qualification
            ):
                raise ValueError('该政策版本曾经生效，请以新版本发布改动')
            return PolicyPublicationResult(status=PublicationStatus.ALREADY_ACTIVATED, target=document.target,
                                           catalog_revision=previous.activated_revision, entry_count=len(document.entries))
            # endregion

        if (
            # 已经为此版本记录过发布尝试。
            previous is not None
            # 上次可能仍有在途 Milvus 操作。
            and previous.write_state is PolicyWriteState.PENDING
            # 维护者尚未确认上次写操作已结束。
            and not confirm_write_ended
        ):

            # region 写入仍待确认时保留现状，等待维护者核对
            raise ValueError('请确认上次发布进程已停止且 Milvus 写入已结束，再使用 --confirm-write-ended')
            # endregion

        # endregion

        # region 保存改变资格的版本，但在同版本规则就绪前停止发布
        write_state = previous.write_state if previous else PolicyWriteState.NOT_STARTED
        if qualification is QualificationConfirmation.CHANGED:
            confirmed_state = PolicyWriteState.FINISHED if write_state is PolicyWriteState.PENDING else write_state
            attempt.record_attempt(approval, write_state=confirmed_state)
            raise ValueError('改变资格的版本须等待同版本结构化规则，目前未生效')
        if (
            # 当前版本已有一份资格确认。
            previous is not None
            # 上一次确认声明原件改变资格。
            and previous.qualification is QualificationConfirmation.CHANGED
            # 本次重试仍使用同一原件，原确认继续有效。
            and previous.source_digest == document.source_digest
        ):
            raise ValueError('该原件已声明改变资格，须等待同版本结构化规则')
        # endregion

        # region 核对索引结构，仅允许复用完整匹配的工单 15 演示基线
        with open_backend() as backend:
            backend.create_collection()
            existing_entries = write_state is PolicyWriteState.NOT_STARTED and backend.has_entries(document.target)
            adopt_baseline = (
                # 当前目标是订单资格所依据的演示文档。
                document.target.document_id == DemoPolicy.DOCUMENT_ID
                # 本次登记的是迁移前已向工单 15 提供检索的版本。
                and document.target.version == DemoPolicy.BASELINE_VERSION
                # 该版本存在由原发布入口写入的历史索引。
                and existing_entries
            )
            if not adopt_baseline:

                # region 仅对未生效版本标记操作在途，清理并全量重发
                if existing_entries:
                    raise ValueError('存在缺少发布记录的政策条目，须核对来源后再发布')
                attempt.record_attempt(approval, write_state=PolicyWriteState.PENDING)
                backend.clear_version(document.target)
                backend.publish(document.entries)
                attempt.finish_write()
                # endregion

            _verify_index(document, backend)
        # endregion

        # region 向量通过验证后提交切换，异常回执由下一次命令先查询历史
        try:
            revision = attempt.activate(active_version, adoption=approval if adopt_baseline else None)
        except PublicationConflict:
            raise
        except Exception:
            raise ValueError('政策切换回执未确认，请用同一发布命令先核对发布记录') from None
        return PolicyPublicationResult(status=PublicationStatus.ACTIVATED, target=document.target,
                                       catalog_revision=revision, entry_count=len(document.entries))
        # endregion
