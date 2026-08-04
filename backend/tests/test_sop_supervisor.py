from __future__ import annotations

from app.db.models import Tool
from app.runtime.contracts import HarnessStructuredOutput, SopAuditOutcome
from app.runtime.sop_supervisor import EvidenceLedger, SopSupervisor, tool_effect_level


def _skill() -> dict[str, object]:
    return {
        "skill_id": "graph_demo",
        "required_info": ["request_type"],
        "nodes": [
            {
                "node_id": "classify",
                "type": "collect_info",
                "instruction": "识别请求类型",
                "expected_user_info": ["request_type"],
                "allowed_actions": ["continue_flow"],
            },
            {
                "node_id": "query_price",
                "type": "tool_call",
                "instruction": "查询价格",
                "expected_user_info": ["product_id"],
                "allowed_actions": ["call_tool:product.price_query"],
            },
            {
                "node_id": "send_notice",
                "type": "tool_call",
                "instruction": "发送通知",
                "expected_user_info": ["recipient"],
                "allowed_actions": ["call_tool:notice.send"],
            },
            {
                "node_id": "reply",
                "type": "response",
                "instruction": "反馈结果",
                "allowed_actions": ["answer_user"],
            },
        ],
        "edges": [
            {
                "source_node_id": "classify",
                "next_node_id": "query_price",
                "predicate_json": {"slot": "request_type", "op": "eq", "value": "price"},
                "priority": 0,
            },
            {
                "source_node_id": "classify",
                "next_node_id": "send_notice",
                "predicate_json": {"slot": "request_type", "op": "eq", "value": "notice"},
                "priority": 1,
            },
            {"source_node_id": "query_price", "next_node_id": "reply"},
            {"source_node_id": "send_notice", "next_node_id": "reply"},
        ],
        "start_node_id": "classify",
        "terminal_node_ids": ["reply"],
    }


def _tools() -> list[Tool]:
    return [
        Tool(
            id="tool_price",
            tenant_id="tenant_demo",
            name="product.price_query",
            method="POST",
            url="https://example.test/query",
            effect_level="read",
        ),
        Tool(
            id="tool_notice",
            tenant_id="tenant_demo",
            name="notice.send",
            method="POST",
            url="https://example.test/send",
            effect_level="write",
        ),
    ]


def test_tool_effect_level_defaults_are_conservative() -> None:
    assert tool_effect_level(Tool(tenant_id="t", name="get", method="GET", url="/")) == "read"
    assert tool_effect_level(Tool(tenant_id="t", name="post", method="POST", url="/")) == "write"
    assert (
        tool_effect_level(
            Tool(
                tenant_id="t",
                name="delete",
                method="DELETE",
                url="/",
                config_json={"requires_confirmation": True},
            )
        )
        == "destructive"
    )


def test_segment_stops_at_branch_and_resolves_predicate_deterministically() -> None:
    supervisor = SopSupervisor()

    classify = supervisor.compile_segment(_skill(), "classify", {}, _tools())
    price = supervisor.compile_segment(
        _skill(), "classify", {"request_type": "price", "product_id": "A1"}, _tools()
    )

    assert classify.node_ids == ["classify"]
    assert classify.boundary == "branch"
    assert price.node_ids == ["classify", "query_price", "reply"]
    assert price.allowed_tool_names == ["product.price_query"]
    assert price.boundary == "terminal"


def test_segment_isolates_write_tool_and_requires_approval() -> None:
    segment = SopSupervisor().compile_segment(
        _skill(), "classify", {"request_type": "notice", "recipient": "ops"}, _tools()
    )

    assert segment.node_ids == ["classify"]
    assert segment.next_step_id == "send_notice"
    assert segment.boundary == "side_effect"

    write_segment = SopSupervisor().compile_segment(
        _skill(), "send_notice", {"recipient": "ops"}, _tools()
    )
    assert write_segment.node_ids == ["send_notice"]
    assert write_segment.requires_approval is True


def test_audit_rejects_claimed_completion_without_required_slot_or_tool_evidence() -> None:
    supervisor = SopSupervisor()
    segment = supervisor.compile_segment(
        _skill(), "query_price", {"product_id": "A1", "request_type": "price"}, _tools()
    )
    output = HarnessStructuredOutput(
        reply="价格是 99 元",
        completed_step_ids=["query_price", "reply"],
    )

    result = supervisor.audit(
        segment,
        _skill(),
        {"product_id": "A1", "request_type": "price"},
        output,
        EvidenceLedger(),
        attempt=0,
        max_repairs=2,
    )

    assert result.outcome == SopAuditOutcome.REPAIR
    assert "tool:product.price_query" in result.missing_evidence
    assert result.repair_contract is not None


def test_audit_accepts_only_declared_slots_and_ignores_graph_claims() -> None:
    supervisor = SopSupervisor()
    segment = supervisor.compile_segment(
        _skill(), "query_price", {"product_id": "A1", "request_type": "price"}, _tools()
    )
    output = HarnessStructuredOutput(
        reply="价格是 99 元",
        slot_updates={"product_id": "A2", "untrusted": "candidate"},
        completed_step_ids=["made_up_step"],
        next_step_id="made_up_step",
    )
    ledger = EvidenceLedger()
    ledger.record_tool_result("product.price_query", {"product_id": "A1"}, True, {"price": 99})

    result = supervisor.audit(
        segment,
        _skill(),
        {"product_id": "A1", "request_type": "price"},
        output,
        ledger,
        attempt=0,
        max_repairs=2,
    )

    assert result.outcome == SopAuditOutcome.PASSED
    assert result.next_step_id is None
    assert result.accepted_slot_updates == {"product_id": "A2"}


def test_audit_fails_closed_after_repair_budget() -> None:
    supervisor = SopSupervisor()
    segment = supervisor.compile_segment(_skill(), "classify", {}, _tools())

    result = supervisor.audit(
        segment,
        _skill(),
        {},
        HarnessStructuredOutput(reply="完成"),
        EvidenceLedger(),
        attempt=2,
        max_repairs=2,
    )

    assert result.outcome == SopAuditOutcome.FAILED
    assert "slot:request_type" in result.missing_evidence


def test_knowledge_step_requires_objective_knowledge_evidence() -> None:
    skill = {
        "skill_id": "knowledge_demo",
        "nodes": [
            {
                "node_id": "policy",
                "type": "knowledge_query",
                "allowed_actions": ["knowledge_query"],
            }
        ],
        "edges": [],
        "terminal_node_ids": ["policy"],
    }
    supervisor = SopSupervisor()
    segment = supervisor.compile_segment(skill, "policy", {}, [])

    missing = supervisor.audit(
        segment,
        skill,
        {},
        HarnessStructuredOutput(reply="政策如下"),
        EvidenceLedger(),
        attempt=0,
        max_repairs=2,
    )
    ledger = EvidenceLedger(knowledge_refs=[{"chunk_id": "chunk-1"}])
    passed = supervisor.audit(
        segment,
        skill,
        {},
        HarnessStructuredOutput(reply="政策如下"),
        ledger,
        attempt=0,
        max_repairs=2,
    )

    assert missing.outcome == SopAuditOutcome.REPAIR
    assert missing.missing_evidence == ["knowledge:policy"]
    assert passed.outcome == SopAuditOutcome.PASSED


def test_handoff_and_confirmation_nodes_are_staffdeck_checkpoints() -> None:
    skill = {
        "skill_id": "checkpoint_demo",
        "nodes": [
            {
                "node_id": "confirm",
                "type": "decision",
                "expected_user_info": ["confirmation"],
                "allowed_actions": ["ask_user"],
            },
            {
                "node_id": "human",
                "type": "handoff",
                "allowed_actions": ["handoff_human"],
            },
        ],
        "edges": [],
    }

    confirmation = SopSupervisor().compile_segment(skill, "confirm", {}, [])
    confirmed = SopSupervisor().compile_segment(
        skill, "confirm", {"confirmation": True}, []
    )
    handoff = SopSupervisor().compile_segment(skill, "human", {}, [])

    assert confirmation.boundary == "human_confirmation"
    assert confirmed.boundary == "checkpoint"
    assert handoff.boundary == "handoff"
