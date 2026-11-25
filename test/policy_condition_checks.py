"""核对政策提取结果与答复或办理实际采用的检索依据。"""

from model.policy.status import PolicyEligibilityOutcome, PolicyEvent, PolicyOrderFact


def extracted_policy_rules(events):
    receipt = None
    for event in events:
        if event['event'] == 'mcp_observation' and 'candidates' in event['result']:
            receipt = event['result']
        if event['event'] == PolicyEvent.ELIGIBILITY_EXTRACTION:
            assert receipt is not None
            for rule in event['extraction']['rules']:
                index = rule['source_index']
                if 0 <= index < len(receipt['candidates']):
                    yield receipt, rule, receipt['candidates'][index]


def assert_complete_policy_extraction(events, citation):
    assert any(
        receipt['event_id'] == citation['event_id']
        and all(item[key] == citation[key] for key in ('document_id', 'version', 'clause_id', 'entry_type'))
        and rule['excerpt'] == citation['excerpt']
        and set(rule['conditions']) == {PolicyOrderFact.OWNED, PolicyOrderFact.PAID, PolicyOrderFact.UNSHIPPED}
        and rule['exceptions'] == [{'conditions': [PolicyOrderFact.REFUNDED]}]
        and rule['outcome'] == PolicyEligibilityOutcome.FULL_ORIGINAL
        for receipt, rule, item in extracted_policy_rules(events)
    ), '实际引用的条款未完整提取限定和例外'
