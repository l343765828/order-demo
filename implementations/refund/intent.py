"""识别本轮退款相关意图、查单目标及独立的退款办理指令。"""

import json
import re
from collections.abc import Callable

from openai import OpenAIError

from config.limits import RefundLimits
from implementations.refund.intent_prompt import INTENT_PROMPT
from implementations.refund.preference_intent import parse_preference_intent
from interfaces.refund.decision import Decide
from model.refund.flow import ConversationDecision, ExchangeMessage
from model.refund.intent import IntentDecision, IntentResolution
from model.refund.status import (
    ConversationIntent, ConversationRole, IntentEvent, OrderGoal, OrderIdExtractionStatus, PolicyRequest,
)

ORDER_NUMBER = re.compile(r"(?<![A-Za-z0-9-])DEMO-[A-Za-z0-9-]+", re.IGNORECASE)
_REFUND_ORDER_LABEL = r"(?:(?:这笔\s*)?(?:订单(?:编号|号)?|编号)\s*(?:是|为)?\s*[：:]?\s*)"
_DIRECT_REFUND = re.compile(
    r"(?:(?:如果)?(?:能退|符合退款条件)(?:[，,]\s*(?:就\s*)?|就\s*))?"
    r"(?:请\s*)?(?:帮我|给我|为我|我要)\s*"
    rf"(?:(?:办理|申请)?\s*退(?:款|掉)?\s*{_REFUND_ORDER_LABEL}?(?P<order>{ORDER_NUMBER.pattern})|"
    rf"(?:办理|申请)\s*(?P<order_first>{ORDER_NUMBER.pattern})\s*的?\s*退款)[。！!]?", re.IGNORECASE,
)
_REFUND_WITHOUT_ORDER = re.compile(
    r"(?:那就\s*)?(?:请\s*)?(?:(?:帮我|给我|为我|我要)\s*)?"
    r"退(?:款|掉)?\s*(?:刚才那[笔单]|之前那[笔单]|上一单)?(?:吧)?[。！!]?$"
)
_REFUND_ORDER_REQUIRED = "请在同一条消息中明确要求退款，并写出唯一的演示订单编号。"
_REFUND_PREFERENCE = re.compile(
    r"[，,]\s*我希望(?:只?退\s*\d+(?:\.\d+)?\s*(?:元|分)(?:到其他账户)?|退到其他账户|换用其他渠道)"
    r"(?:[，,]\s*(?:使用|换用)其他渠道)?[。！!]?$"
)
_REFUND_CONDITION = re.compile(r"只有|否则|才(?:退|办|同意|可以)|必须|前提|不然")
_POLICY_REFERENCE = re.compile(r"政策|条款|条文|依据|规定|规则|原文|细则|官方文件|哪一?条")
_POLICY_REVIEW_WORDS = re.compile(r"先看|看完|读完")
_POLICY_BASIS = rf"(?:这笔订单的|适用的?|正式|官方|相关|退款|政策|哪一?条)*(?:{_POLICY_REFERENCE.pattern})"
_INDEPENDENT_POLICY_QUESTION = re.compile(
    r"[，,。；;]\s*(?:并且?|顺便|同时|另(?:外)?|然后)\s*(?:请\s*)?"
    rf"(?:(?:告诉我|说明|解释|提供|附上|问)\s*{_POLICY_BASIS}|(?:把|将)\s*{_POLICY_BASIS}\s*(?:发给我|给我看))"
    r"[。！？!?]?$"
)
_DEFERRED_REFUND_WORDS = re.compile(
    r"如果|要是|如能|等(?:我|你|到)|等待|待核实|先|看完|再(?:决定|考虑)|前提|找[到得]|查[到得出]"
)
_REFUND_OVERRIDE = re.compile(r"其他账户|其他渠道|只退|退\s*\d+")
_REFUND_REQUEST_WORDS = re.compile(
    r"(?:帮我|给我|为我|我(?:要|想|需要|申请)|请|直接|立即)\s*"
    r"(?:(?:帮我|给我|为我|把|将|对|申请|办理|提交|执行|直接)\s*){0,3}"
    rf"(?:{ORDER_NUMBER.pattern}\s*的?\s*)?退(?:款|掉|一下)?|"
    r"(?:^|[，,。；;])\s*(?:申请|办理|帮忙办理)\s*退款|"
    rf"^\s*(?:请\s*)?退(?:款|掉)?\s*(?={ORDER_NUMBER.pattern}[。！!]?\s*$)",
    re.IGNORECASE,
)
_REFUND_NEGATION = re.compile(
    r"(?:不要|不用|无需|先别|先不|暂不|不需要|不想|取消|别)\s*(?:(?:再|帮我|办理|申请|提交|执行|给我|为我)\s*)*"
    rf"(?:{ORDER_NUMBER.pattern}\s*的?\s*)?退(?:款)?", re.IGNORECASE,
)
# 仅检查候选措辞或编号前的引导语，客服事实说明后的独立指令仍可授权。
_REFUND_NON_DIRECTIVE_PREFIX = re.compile(
    r"(?:(?:假[设如]|假定|设想)(?!\s*(?:能退|符合退款条件)\s*[，,])|"
    r"(?:如果|要是)\s*(?:我|你|有人|别人)\s*(?:说|问|请|请求|要求|要|想|提出|$))"
    r"[^。！？!?；;\n]*$|"
    r"(?:举(?:个|一个)?例(?:子)?|比如说|比方说|例如|"
    r"(?:有人|别人|客服|朋友|同事)(?:刚才|之前|曾经)?(?:说|问|提到))"
    r"(?:\s*[：:][^。！？!?；;\n]*|\s*[，,]?\s*)$"
)

_QUOTED_TEXT = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"]*"' r"|'[^'\n]*'")
_REFUND_ORDER_AFTER_REQUEST = re.compile(rf"\s*[，,：:]?\s*{_REFUND_ORDER_LABEL}?")
_REFUND_ORDER_TOPIC = re.compile(rf"(?:^|[。；;！？!?\n，,])\s*(?:关于\s*|{_REFUND_ORDER_LABEL})?$")
_REFUND_ORDER_BEFORE_REQUEST = re.compile(r"\s*[，,：:]?\s*")


# 识别当前消息意图，返回已核对的查单目标、补号问题或直接回复。
def resolve_intent(
    history: list[ExchangeMessage],
    pending: str | None,
    decide: Decide,
    emit: Callable[..., None],
) -> IntentResolution:

    # region 读取最新消费者消息并初始化意图结果
    current = history[-1].content
    resolution = IntentResolution(question=current)
    # endregion

    # region 会话累计正文超限时请消费者开启新会话
    if sum(len(m.content) for m in history) > RefundLimits.MAX_CONVERSATION_HISTORY_CHARS:
        resolution.reply = "当前会话内容过长，请开启新会话后再试。"
        return resolution
    # endregion

    # region 从本轮原文提取明确的偏好命令和独立业务请求
    preferences = parse_preference_intent(current)
    current = preferences.business_text
    resolution.question = current
    # endregion

    # region 应用本轮快速路径或调用模型识别意图和订单号
    extraction_status = OrderIdExtractionStatus.MISSING
    continuing_eligibility = False
    policy_question = _INDEPENDENT_POLICY_QUESTION.search(current.strip())
    command = current.strip()[:policy_question.start()] if policy_question is not None else current
    direct_refund = _direct_refund_order(command)
    if not current:

        # region 纯偏好请求或临时详略没有业务调查目标
        decision = ConversationDecision(intent=ConversationIntent.MEMORY_REQUEST, order_id=None)
        # endregion

    elif (
        # 会话中有一条退款资格咨询正在等待消费者补充订单号。
        pending
        # 本轮消息只包含一个格式可识别的订单号。
        and ORDER_NUMBER.fullmatch(current.strip())
        # 补充的订单号未超过长度上限。
        and len(current.strip()) <= RefundLimits.MAX_ORDER_ID_CHARS
    ):

        # region 只收到合规订单号时接续待补号的资格咨询
        decision = ConversationDecision(intent=ConversationIntent.ORDER_QUESTION, order_id=current.strip().upper(),
                                        order_goal=OrderGoal.ELIGIBILITY)
        resolution.question = pending
        # endregion

    elif direct_refund is not None:

        # region 直接识别明确办理指令及其独立附带的依据问题
        decision = ConversationDecision(intent=ConversationIntent.REFUND_REQUEST,
                                        order_id=direct_refund,
                                        policy_request=PolicyRequest.BASIS if policy_question is not None else None)
        # endregion

    elif _REFUND_WITHOUT_ORDER.fullmatch(current.strip()):

        # region 当前办理指令缺号或只指代历史订单时直接要求重新明确目标
        decision = ConversationDecision(intent=ConversationIntent.REFUND_REQUEST, order_id=None)
        resolution.reply = _REFUND_ORDER_REQUIRED
        # endregion

    else:

        # region 其他消息由模型识别本轮意图和订单号
        prompt = INTENT_PROMPT.substitute(
            pending_state="是" if pending else "否",
            dialogue_json=json.dumps(
                [*(m.model_dump(mode="json") for m in history[:-1]),
                 ExchangeMessage(role=ConversationRole.USER, content=current).model_dump(mode="json")],
                ensure_ascii=False,
            ),
        )
        try:
            selected = decide(IntentDecision, prompt)
        except (ValueError, OpenAIError):

            # region 业务识别失败时保留已明确解析的独立偏好命令
            if preferences.command is None:

                # region 没有可独立处理的偏好命令时使用原有流程降级
                raise
                # endregion

            decision = ConversationDecision(intent=ConversationIntent.UNCLEAR, order_id=None)
            resolution.eligibility_question = pending
            resolution.reply = "本轮未能识别业务请求，请稍后再试。"
            # endregion

        else:

            # region 单独记录模型提取结果并转入既有业务决定格式
            extraction_status = selected.order_id_status
            emit(IntentEvent.ORDER_ID_EXTRACTION.value, status=extraction_status.value,
                 order_id=selected.order_id, evidence=selected.order_id_evidence)
            decision = _policy_intent(current, selected)
            continuing_eligibility = selected.continues_eligibility_question
            if (
                # 模型确认本轮是在补充原资格咨询的编号。
                continuing_eligibility
                # 当前会话确有一条仍待补号的原问题。
                and pending is not None
                # 本轮只是补号，未开启新的政策调查。
                and decision.policy_request is None
            ):

                # region 用已保存的问题接续资格咨询
                resolution.question = pending
                # endregion

            # endregion

        # endregion

    # endregion

    # region 记录本轮意图并更新识别结果
    decision.memory_request = preferences.command
    decision.temporary_detail = preferences.temporary_detail
    emit(IntentEvent.DECISION.value, decision=decision.model_dump())
    resolution.decision = decision
    if resolution.reply is not None:

        # region 已生成业务识别失败提示时直接交给固定偏好节点
        return resolution
        # endregion

    # endregion

    # region 按意图校验查单目标，必要时生成直接回复或保存待补单问题
    match decision.intent:
        case ConversationIntent.MEMORY_REQUEST:

            # region 纯偏好命令不启动调查，无法识别操作时说明可维护的范围
            resolution.eligibility_question = pending
            if decision.memory_request is None:
                resolution.reply = "可以查看本人的长期偏好，或明确要求记住、修改、删除订单状态答复的详略。"
            return resolution
            # endregion

        case ConversationIntent.POLICY_QUESTION:

            # region 通用政策咨询使用已分离的业务原文
            return resolution
            # endregion

        case ConversationIntent.REFUND_REQUEST:

            # region 明确办理仅绑定本轮唯一且有效的订单号
            candidates = {match.upper() for match in ORDER_NUMBER.findall(current)}
            unquoted = _QUOTED_TEXT.sub(_mask_refund_quote, current)
            directives = [
                request
                for request in _REFUND_REQUEST_WORDS.finditer(unquoted)
                if _REFUND_NON_DIRECTIVE_PREFIX.search(unquoted[:request.start()]) is None
            ]
            if (
                # 没有消费者本轮独立发出的办理指令，引用、假设或转述不产生授权。
                not directives
                # 本轮还表达了不办理或撤销，无法确认本次授权。
                or _REFUND_NEGATION.search(unquoted) is not None
                # 消费者提及政策后仍无法确认有独立的当轮办理指令。
                or _depends_on_policy(current)
                # 消费者将金额、账户或渠道变更设为办理的条件。
                or (
                    # 当前表达含有前提或限制。
                    _REFUND_CONDITION.search(unquoted) is not None
                    # 这些限制涉及不同的金额、账户或渠道。
                    and _REFUND_OVERRIDE.search(unquoted) is not None
                )
            ):

                # region 办理意图不明确时追问是否要办理
                resolution.reply = "请明确说明是否要办理这笔订单的退款。"
                return resolution
                # endregion

            identifiable_orders = {
                match[0].upper()
                for match in ORDER_NUMBER.finditer(unquoted)
                if any(_binds_refund_order(unquoted, request, match) for request in directives)
            }
            if (
                # 消费者未指定唯一的演示订单。
                len(candidates) != 1
                # 模型没有识别到本次办理目标。
                or decision.order_id is None
                # 识别出的编号没有与本轮独立办理指令关联。
                or decision.order_id.upper() not in identifiable_orders
                # 当前订单号超出业务字段长度。
                or len(decision.order_id) > RefundLimits.MAX_ORDER_ID_CHARS
            ):

                # region 未指定唯一有效的办理编号时请消费者重述目标
                resolution.reply = (
                    "请明确指定一个要办理退款的演示订单编号。" if len(candidates) > 1
                    else _REFUND_ORDER_REQUIRED
                )
                # endregion

            else:

                # region 核验通过的办理编号统一为大写
                decision.order_id = decision.order_id.upper()
                # endregion

            # endregion

        case ConversationIntent.UNCLEAR:

            # region 意图不清时询问是咨询资格还是请求办理
            resolution.reply = "请问你想咨询这笔订单能否退款，还是要办理退款？"
            # endregion

        case ConversationIntent.ORDER_QUESTION:

            # region 只读查询采用模型选定的编号，缺号或歧义时向消费者追问
            if decision.order_id is not None:

                # region 模型或快速补号路径已确定订单，统一使用大写编号
                decision.order_id = decision.order_id.upper()
                # endregion

            elif extraction_status is OrderIdExtractionStatus.AMBIGUOUS:

                # region 目标不明确时请指定一笔订单，接续中的资格问题继续等待补号
                resolution.eligibility_question = pending if continuing_eligibility else None
                resolution.reply = "请明确指定一个要查询的演示订单编号。"
                # endregion

            elif decision.policy_request is not None:

                # region 订单政策咨询须重新明确本轮要核实的订单和政策问题
                resolution.reply = "请提供要咨询政策依据的演示订单编号，并说明要核实的问题。"
                # endregion

            elif decision.order_goal is OrderGoal.ELIGIBILITY:

                # region 缺号的资格咨询保存问题，供本会话补号后接续
                resolution.eligibility_question = resolution.question
                resolution.reply = "请提供要咨询资格的演示订单编号。"
                # endregion

            else:

                # region 查订单状态缺号时要求提供本轮编号
                resolution.reply = "请提供一个要查询的演示订单编号。"
                # endregion

            # endregion

        case _:

            # region 其他意图提示可咨询政策或查询订单
            resolution.reply = "请告诉我要查询的订单或退款政策问题。"
            # endregion

    return resolution
    # endregion


# 采用本轮政策需求，并阻止有政策前提的请求进入办理。
def _policy_intent(current: str, selected: IntentDecision) -> ConversationDecision:

    # region 将模型识别结果转为本轮意图，并用消费者原文核对政策前提
    mentions_policy = _POLICY_REFERENCE.search(current) or _POLICY_REVIEW_WORDS.search(current)
    decision = ConversationDecision(intent=selected.intent, order_id=selected.order_id, order_goal=selected.order_goal,
                                    policy_request=selected.policy_request if mentions_policy else None)
    depends_on_policy = _depends_on_policy(current)
    # endregion

    # region 将先看条款和独立政策问题转入只读调查
    if (
        # 消费者把条款设为办理前提，或表示看完条款再决定。
        decision.policy_request is PolicyRequest.BEFORE_REFUND
        # 模型提取了依据问题，但办理部分无法独立确认。
        or (
            # 模型同时识别为办理。
            decision.intent is ConversationIntent.REFUND_REQUEST
            # 原文没有可独立核验的办理指令。
            and depends_on_policy
            # 仍能从模型结果或原文确认消费者提出了政策问题。
            and (
                # 模型明确提取了政策问题。
                decision.policy_request is PolicyRequest.BASIS
                # 消费者原文指出办理必须等待政策核实。
                or _DEFERRED_REFUND_WORDS.search(current) is not None
            )
        )
    ):

        # region 有政策前提时保留订单咨询的目标，通用政策问题无须补号
        decision.policy_request = PolicyRequest.BEFORE_REFUND
        if (
            # 消费者明确询问某笔订单，即使缺号也须追问。
            selected.intent is ConversationIntent.ORDER_QUESTION
            # 已提供目标或存在多个待澄清的订单。
            or selected.order_id_status is not OrderIdExtractionStatus.MISSING
        ):
            decision.intent = ConversationIntent.ORDER_QUESTION
        else:
            decision.intent = ConversationIntent.POLICY_QUESTION
        # endregion

    if (
        # 需要按本人订单事实解释条款的适用性。
        decision.intent is ConversationIntent.ORDER_QUESTION
        # 本轮明确提出了政策问题。
        and decision.policy_request is not None
    ):

        # region 用资格调查读取本人订单事实，供正式条款核验
        decision.order_goal = OrderGoal.ELIGIBILITY
        # endregion

    return decision
    # endregion


# 遮盖转述或举例内容，同时保留单独用引号标示的订单号。
def _mask_refund_quote(quote: re.Match[str]) -> str:

    # region 引用正文作为句子边界，单独的订单号仍需与办理指令关联
    content = quote[0][1:-1].strip()
    return f" {content} " if ORDER_NUMBER.fullmatch(content) else "。"
    # endregion


# 确认混合请求中存在独立于政策的办理指令，避免将政策前提视为当前授权。
def _depends_on_policy(text: str) -> bool:

    # region 普通办理无需额外区分政策意图
    if _POLICY_REFERENCE.search(text) is None:

        # region 当前消息未提及政策
        return False
        # endregion

    # endregion

    # region 只有明确办理后另附单独的依据问题时才识别为当轮独立授权
    message = text.strip()
    question = _INDEPENDENT_POLICY_QUESTION.search(message)
    if question is None:

        # region 混合请求未明确拆分时等待消费者重新授权
        return True
        # endregion

    command = message[:question.start()]
    if _direct_refund_order(command) is not None:

        # region 前段本身是明确指令，后段只补充索要依据
        return False
        # endregion

    # region 前段仍有政策或等待条件时不得采用后段问题来证明办理独立
    return _POLICY_REFERENCE.search(command) is not None or _DEFERRED_REFUND_WORDS.search(command) is not None
    # endregion
    # endregion


# 判定编号是否为这条办理指令的目标，避免采用其他请求中的编号。
def _binds_refund_order(text: str, request: re.Match[str], order: re.Match[str]) -> bool:

    # region 核对编号与办理指令的相对位置和连接内容
    if request.end() <= order.start():

        # region 指令后紧接订单号或编号标签时绑定目标
        return _REFUND_ORDER_AFTER_REQUEST.fullmatch(text[request.end():order.start()]) is not None
        # endregion

    if order.end() <= request.start():

        # region 先单独交代书面编号再明确办理时绑定该订单
        return (
            _REFUND_ORDER_TOPIC.search(text[:order.start()]) is not None
            and _REFUND_ORDER_BEFORE_REQUEST.fullmatch(text[order.end():request.start()]) is not None
        )
        # endregion

    # region 指令本身包含订单号时直接绑定目标
    return True
    # endregion


# 识别明确单笔办理指令及独立的退款方式偏好，附退款条件时交给意图识别。
def _direct_refund_order(current: str) -> str | None:

    # region 将独立金额或去向偏好与明确的办理指令分开读取
    text = current.strip()
    preference = _REFUND_PREFERENCE.search(text)
    if preference is not None:

        # region 只识别独立的金额或去向偏好，额外请求仍交给意图识别
        text = text[:preference.start()].strip()
        # endregion

    match = _DIRECT_REFUND.fullmatch(text)
    if match is None:
        return None
    return (match["order"] or match["order_first"]).upper()
    # endregion
