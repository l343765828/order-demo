"""从当前消费者消息解析有限的偏好命令及临时表达要求。"""

import re

from model.refund.preference import (
    DeletePreference, ListPreferences, PreferenceAction, PreferenceIntent, PreferenceKey,
    ResponseDetail, SetPreference,
)

# 指令以分句边界或并列连接词开始，以当前分句结束。引用中的请求不参与匹配。
_START = r"(?:(?:^|(?<=[，,。；;！？!?\n]))\s*(?:顺便|并且|同时)?|(?:顺便|并且|同时))\s*(?:也?请你?)?\s*(?:帮我|麻烦你)?\s*"
_END = r"\s*(?=$|[，,。；;！？!?\n])"
_QUOTED = re.compile(r"“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|\"[^\"]*\"")
_STYLE = r"(?:(?P<brief>简短|简洁|简要|精简|简明|简略)|(?P<detailed>详细|详尽|逐项|分项))"
_ANSWER = r"(?:方式|一点|地)?(?:回答|回复|答复|说明)"
_KEY = r"(?:我的)?(?:(?:订单状态|订单)?(?:答复|回答|回复)(?:详略)?(?:偏好)?|(?:长期)?(?:详略)?偏好)"
_LIST = re.compile(_START + r"(?:(?:查看|列出|显示|告诉我|查询)(?:一下)?(?:我(?:的|保存的)?|当前)?(?:长期)?偏好(?:是什么|有哪些)?|我(?:现在)?(?:有|保存了)哪些(?:长期)?偏好)" + _END)
_SET = re.compile(
    _START + r"(?:(?:记住|记下|保存)\s*[，,：:]?\s*(?:我希望|我想要)?\s*(?:以后|从今以后|今后|长期)?\s*(?:都)?(?:用中文|用)?\s*"
    rf"|(?:把|将)?{_KEY}(?:更正|修改|更新|改|设置|设)(?:为|成)|(?:更正|修改|更新|设置){_KEY}(?:为|成))\s*"
    + _STYLE + _ANSWER + _END
)
_DELETE = re.compile(_START + rf"(?:忘掉|忘记|删除|清除|移除)(?:一下)?{_KEY}" + _END)
_TEMPORARY = re.compile(_START + r"(?:(?:这次|本次|本轮|这轮|当次|这回|只要)\s*(?:请|帮我)?\s*)?(?:用)?\s*" + _STYLE + _ANSWER + _END)
_PASSIVE = re.compile(r"我(?:平时|通常|一般)?(?:更)?(?:喜欢|习惯|偏好)(?:比较)?" + _STYLE + r"(?:的)?" + _ANSWER)
_OTHER_LIST = re.compile(_START + r"(?:查看|列出|查询|告诉我)(?:其他|别的|另一位|消费者|用户)[^，,。；;！？!?\n]*(?:长期)?偏好" + _END)
_NON_DIRECTIVE = re.compile(
    r"举(?:个|一个)?例|(?:这|那)?只(?:是|作为)(?:个|一个)?(?:示例|例子|假设)|比如说|比方说|例如|假[设如]|如果我说|"
    r"(?:别人|有人|同事|朋友|他|她|他们)(?:刚才|之前|曾经)?(?:说|问|提到)|"
    r"不(?:是|在)下指令|不是在下指令|(?:不要|不用|别)(?:真的|实际|帮我)?(?:记住|删除|保存|忘记)"
)


# 移除已识别指令，只把其余当前消息交给业务意图和调查节点。
def parse_preference_intent(current: str) -> PreferenceIntent:
    # region 保留原文索引并遮盖引用，避免把转述当成新指令
    unquoted = current
    for quoted in _QUOTED.finditer(current):
        unquoted = unquoted[:quoted.start()] + "\0" * len(quoted[0]) + unquoted[quoted.end():]
    result = PreferenceIntent(business_text=current)
    spans: list[tuple[int, int]] = []
    # endregion

    # region 分离明确的长期命令，同句多次管理时采用最后一条
    has_explicit_instruction = _NON_DIRECTIVE.search(unquoted) is None
    commands = sorted(
        (match.start(), action, match)
        for action, pattern in ((PreferenceAction.LIST, _LIST), (PreferenceAction.SET, _SET), (PreferenceAction.DELETE, _DELETE))
        for match in pattern.finditer(unquoted)
        if has_explicit_instruction
    )
    spans.extend(match.span() for _, _, match in commands)
    if commands:
        _, action, match = commands[-1]
        match action:
            case PreferenceAction.LIST:
                result.command = ListPreferences(action=action)
            case PreferenceAction.SET:
                detail = ResponseDetail.BRIEF if match.group("brief") else ResponseDetail.DETAILED
                result.command = SetPreference(action=action, key=PreferenceKey.RESPONSE_DETAIL, value=detail)
            case PreferenceAction.DELETE:
                result.command = DeletePreference(action=action, key=PreferenceKey.RESPONSE_DETAIL)
    # endregion

    # region 解析本轮表达要求，临时详略不形成持久化命令
    for match in _TEMPORARY.finditer(unquoted):
        if any(start <= match.start() < end for start, end in spans):
            # region 长期指令已经消费其所含的详略措辞
            continue
            # endregion
        result.temporary_detail = ResponseDetail.BRIEF if match.group("brief") else ResponseDetail.DETAILED
        spans.append(match.span())
    # endregion

    # region 移除已解析的分句，保留政策问题的疑问标点及订单事实原文
    if spans:
        business = current
        for start, end in sorted(spans, reverse=True):
            business = business[:start] + business[end:].lstrip("，,。；;！？!?\n ")
        result.business_text = business.strip().strip("，,；;").strip()
    elif (
        # 当前消息只是陈述平时喜欢的答复详略。
        _PASSIVE.fullmatch(unquoted.strip().rstrip("。！!"))
        # 当前消息试图查看其他消费者的偏好。
        or _OTHER_LIST.fullmatch(unquoted.strip().rstrip("。！!？?"))
    ):
        # region 只有普通陈述或越范围请求时提示可管理的本人偏好
        result.business_text = ""
        # endregion
    return result
    # endregion
