from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from app.agent_team_migration import (
    AGENT_TOOL_BINDINGS,
    MIGRATION_VERSION,
    SOP_TEMPLATES,
    StaffDeckApi,
    TENANT_ID,
    _credentials,
    tool_update_payload,
)


TARGET_AGENT_NAME = "QQQ · Claude"
TARGET_SOP_ID = "agent_team_amazon_research"
TARGET_TOOL_NAMES = tuple(AGENT_TOOL_BINDINGS["cc-amz"])

QQQ_PERSONA_PROMPT = """# 角色
你是 QQQ，一名 Amazon 运营与跨境选品 Agent。你的首要任务是用可追溯数据帮助用户判断市场、产品和竞品机会；PPC 广告分析是次级能力。没有广告后台数据时，不得声称掌握 ACoS、ROAS、CTR、CPC、花费或订单。

# 工作方式
- 普通咨询、方案解释和探索性讨论直接回答，不为展示流程而启动 SOP。
- 需要外部市场事实、完整竞品研究或可审计选品结论时，进入“Amazon 选品与竞品研究”SOP。
- 快速问题用 L1：取得足以回答的核心证据后停止。标准竞品/品类研究用 L2。用户明确要求完整全案或进场决策时才用 L3。
- 不重复更换近义关键词试探数据；工具空结果或失败时更换合适数据源，仍无数据就明确记录缺口。

# 数据原则
- SellerSprite 用于 Amazon 市场、ASIN、关键词、集中度和评论摘要；Keepa 用于价格与 BSR 历史；Sorftime 用于历史时段、评论、类目以及 Walmart、TikTok、1688 补充。
- 每个关键数字标注来源、数据日期和口径。严格区分工具事实、分析推断和未知信息，不编造数字、趋势、图片或工具成功。
- 多个 ASIN 优先批量或一次性规划调用；信息足够就停止，避免为了“完整”穷举工具。

# 输出
先给“做 / 不做 / 谨慎做”及一句话理由，再给关键证据、机会、风险、数据缺口和下一步。L1 保持简短；L2 输出标准 EvidencePack；L3 输出完整决策报告。用户没有要求时，不输出内部流程说明、Graph 状态或冗长自我介绍。"""


def _target_sop_content() -> dict[str, Any]:
    content = copy.deepcopy(SOP_TEMPLATES["cc-amz"])
    content["name"] = "Amazon 选品与竞品研究"
    return content


def _metadata(current: dict[str, Any]) -> dict[str, Any]:
    return {
        **current,
        "role_name": "Amazon 运营与跨境选品 Agent",
        "role_kind": "specialist",
        "team": "cross-border-ecommerce",
        "work_styles": ["证据优先", "够用即止", "按需进入 SOP"],
        "expertise_tags": [
            "Amazon 选品",
            "市场研究",
            "竞品分析",
            "关键词策略",
            "跨平台验证",
            "Amazon PPC",
        ],
        "system_prompt_summary": "证据驱动的 Amazon 运营、选品与竞品研究 Agent",
        "default_runtime_mode": "claude_supervised",
        "runtime_mode_locked": True,
        "work_modes": ["自由分析", "L1/L2/L3 选品研究", "SOP 监督执行"],
        "capability_profile_source": "agent-team:cc-amz",
        "capability_profile_version": MIGRATION_VERSION,
    }


def _ui_config_payload(row: dict[str, Any]) -> dict[str, Any]:
    allowlist = list(dict.fromkeys([*(row.get("claude_skill_allowlist") or []), TARGET_SOP_ID]))
    return {
        "tenant_id": row["tenant_id"],
        "show_thinking_trace": row.get("show_thinking_trace", True),
        "show_skill_trace": row.get("show_skill_trace", True),
        "show_tool_trace": row.get("show_tool_trace", True),
        "reflection_max_rounds": row.get("reflection_max_rounds", 1),
        "agent_loop_max_actions": max(8, int(row.get("agent_loop_max_actions") or 6)),
        "claude_runtime_enabled": row.get("claude_runtime_enabled") is True,
        "claude_model_config_id": row.get("claude_model_config_id"),
        "claude_skill_allowlist": allowlist,
        "claude_max_repair_rounds": row.get("claude_max_repair_rounds", 2),
    }


def inspect_alignment(api: StaffDeckApi) -> dict[str, Any]:
    agents = api.request("GET", api.query_path("/api/enterprise/agents", tenant_id=api.tenant_id))
    matches = [row for row in agents if row.get("name") == TARGET_AGENT_NAME]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {TARGET_AGENT_NAME} agent; found {len(matches)}")
    tools = api.request("GET", api.query_path("/api/enterprise/tools", tenant_id=api.tenant_id))
    tools_by_name = {str(row.get("name")): row for row in tools}
    missing = [name for name in TARGET_TOOL_NAMES if name not in tools_by_name]
    unclassified = [
        name
        for name in TARGET_TOOL_NAMES
        if name in tools_by_name and tools_by_name[name].get("effect_level") != "read"
    ]
    return {
        "agent": matches[0],
        "tools_by_name": tools_by_name,
        "missing_tools": missing,
        "tools_to_classify": unclassified,
    }


def apply_alignment(api: StaffDeckApi) -> dict[str, Any]:
    inspection = inspect_alignment(api)
    agent = inspection["agent"]
    updated_agent = api.request(
        "PUT",
        f"/api/enterprise/agents/{agent['id']}",
        {
            "tenant_id": api.tenant_id,
            "name": TARGET_AGENT_NAME,
            "description": "Amazon 运营与跨境选品 Agent（市场、竞品、关键词、1688 与跨平台验证）",
            "persona_prompt": QQQ_PERSONA_PROMPT,
            "status": "active",
            "metadata": _metadata(agent.get("metadata") or {}),
        },
    )

    skills = api.request("GET", api.query_path("/api/enterprise/skills", tenant_id=api.tenant_id))
    skill = next((row for row in skills if row.get("skill_id") == TARGET_SOP_ID), None)
    if not skill:
        raise RuntimeError(f"Required SOP is missing: {TARGET_SOP_ID}")
    updated_skill = api.request(
        "PUT",
        f"/api/enterprise/skills/{TARGET_SOP_ID}",
        {"tenant_id": api.tenant_id, "content": _target_sop_content(), "status": "published"},
    )

    tools_by_name = inspection["tools_by_name"]
    for name in inspection["tools_to_classify"]:
        row = tools_by_name[name]
        tools_by_name[name] = api.request(
            "PUT",
            f"/api/enterprise/tools/{row['id']}",
            tool_update_payload(row, effect_level="read"),
        )

    current_resources = api.request(
        "GET",
        api.query_path(
            f"/api/enterprise/agents/{agent['id']}/resources", tenant_id=api.tenant_id
        ),
    )
    resources = [row for row in current_resources if row.get("resource_type") != "tool"]
    if not any(
        row.get("resource_type") == "skill" and row.get("resource_id") == updated_skill["id"]
        for row in resources
    ):
        resources.append(
            {"resource_type": "skill", "resource_id": updated_skill["id"], "status": "active"}
        )
    resources.extend(
        {
            "resource_type": "tool",
            "resource_id": tools_by_name[name]["id"],
            "status": "active",
            "metadata": {
                "capability_profile_source": "agent-team:cc-amz",
                "capability_profile_version": MIGRATION_VERSION,
            },
        }
        for name in TARGET_TOOL_NAMES
        if name in tools_by_name
    )
    api.request(
        "PUT",
        f"/api/enterprise/agents/{agent['id']}/resources",
        {"tenant_id": api.tenant_id, "resources": resources},
    )

    ui_config = api.request(
        "GET", api.query_path("/api/enterprise/ui-config", tenant_id=api.tenant_id)
    )
    api.request("PUT", "/api/enterprise/ui-config", _ui_config_payload(ui_config))
    return {
        "agent_id": updated_agent["id"],
        "sop_id": TARGET_SOP_ID,
        "bound_tools": len(TARGET_TOOL_NAMES) - len(inspection["missing_tools"]),
        "missing_tools": inspection["missing_tools"],
        "classified_read_only": len(inspection["tools_to_classify"]),
        "version": MIGRATION_VERSION,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Align QQQ Claude with the cc-amz capability profile")
    parser.add_argument("--base-url", default="http://127.0.0.1:18173")
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    username, password = _credentials(args.credential_file)
    api = StaffDeckApi(args.base_url, TENANT_ID)
    api.login(username, password)
    if args.apply:
        result = {"status": "applied", **apply_alignment(api)}
    else:
        inspection = inspect_alignment(api)
        result = {
            "status": "dry-run",
            "agent_id": inspection["agent"]["id"],
            "target_tools": len(TARGET_TOOL_NAMES),
            "missing_tools": inspection["missing_tools"],
            "tools_to_classify": len(inspection["tools_to_classify"]),
            "version": MIGRATION_VERSION,
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
