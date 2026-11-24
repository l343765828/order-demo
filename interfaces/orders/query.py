"""本人查单服务的调用契约。"""

from typing import Protocol

from model.orders.query import GetOrderRequest, OrderQueryResult


# 接收已认证的消费者身份与订单请求，返回 MCP 使用的查单结果。
class OrderQueryService(Protocol):
    # 读取该消费者的订单及模拟退款状态，隐藏未拥有的订单与数据库详情。
    def query_order(self, request: GetOrderRequest, *, authenticated_consumer_id: str) -> OrderQueryResult:

        # region 声明本人查单的实现入口
        ...
        # endregion
