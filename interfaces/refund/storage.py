"""退款执行与提交记录的存储接口。"""

from typing import Protocol

from model.refund.submission import (
    AuthorizedRefundIntent, GetRefundResultRequest, RefundLookupResult, RefundPolicyVerification,
    RefundResult, SubmitMockRefundRequest,
)


# 为 MCP 工具提供本人原退款核对和受控执行。
class RefundTransactionService(Protocol):
    # 按认证消费者查询本人订单已有的退款。
    def lookup_refund(self, request: GetRefundResultRequest, *,
                      authenticated_consumer_id: str) -> RefundLookupResult:

        # region 声明只读核对契约
        ...
        # endregion

    # 依据已持久的本次授权执行整单模拟退款。
    def submit_refund(self, request: SubmitMockRefundRequest, *, authenticated_consumer_id: str) -> RefundResult:

        # region 声明单次退款执行契约
        ...
        # endregion


# 为 Host 管理未决拦截、提交保存及结果回写。
class RefundDispatchService(Protocol):
    # 查找当前消费者订单的未决提交键，查无记录时返回 None。
    def blocking_dispatch(self, authorization: AuthorizedRefundIntent) -> str | None:

        # region 声明未决提交读取契约
        ...
        # endregion

    # 保存当前提交，只有确认新记录已提交才返回 True。
    def reserve_dispatch(self, event: RefundPolicyVerification) -> bool:

        # region 声明提交保存契约
        ...
        # endregion

    # 按数据库中的期望前态保存本次退款结果。
    def record_dispatch_result(self, key: str, result: RefundResult) -> None:

        # region 声明提交结果回写契约
        ...
        # endregion
