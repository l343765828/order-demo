"""在写入退款图检查点时升级继承的旧金额通道。"""

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

from model.refund.flow import legacy_amount_updates


# 保存退款图快照，避免新快照引用历史版本中仍携带币种的金额通道。
class RefundCheckpointSaver(PyMySQLSaver):
    # 按 LangGraph 契约持久化快照及新增通道版本，返回本次写入的快照位置。
    def put(
        self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:

        # region 检查本次快照是否继承含旧币种的通道
        updates = legacy_amount_updates(checkpoint["channel_values"])
        if not updates:

            # region 无历史值时沿用默认写入路径
            return super().put(config, checkpoint, metadata, new_versions)
            # endregion

        # endregion

        # region 为规范化后的金额另存通道版本并保留已见状态
        previous = checkpoint["channel_versions"]
        versions = {name: self.get_next_version(str(previous[name]), None) for name in updates}
        seen = {
            node: {name: versions.get(name, version) if previous.get(name) == version else version
                   for name, version in channels.items()}
            for node, channels in checkpoint["versions_seen"].items()
        }
        normalized = checkpoint.copy()
        normalized["channel_values"] = {**checkpoint["channel_values"], **updates}
        normalized["channel_versions"] = {**previous, **versions}
        normalized["versions_seen"] = seen
        # endregion

        # region 仅写入新快照及新通道版本，历史检查点保留原内容
        return super().put(config, normalized, metadata, {**new_versions, **versions})
        # endregion
