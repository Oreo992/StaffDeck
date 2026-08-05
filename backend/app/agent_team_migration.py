from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


TENANT_ID = "tenant_demo"
MIGRATION_SOURCE = "agent-team"
MIGRATION_VERSION = "1.1.0"
LEGACY_PATH_PATTERN = re.compile(r"(?:/opt/cc-base|\$HOME|~)/\.claude(?:/[A-Za-z0-9._${}/-]+)?")
SECRET_JSON_PATTERN = re.compile(
    r'(?i)("[^"]*(?:secret|api[_-]?key|access[_-]?token|password)[^"]*"\s*:\s*)"[^"]*"'
)
SECRET_ENV_DEFAULT_PATTERN = re.compile(
    r'(os\.environ\.get\(["\'](?:[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*)["\']\s*,\s*)["\'][^"\']*["\'](\s*\))'
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r'(?m)^((?:DEFAULT_)?(?:API_KEY|APP_SECRET|ACCESS_TOKEN|PASSWORD)\s*=\s*)["\'][^"\']*["\']\s*$'
)
SKIPPED_FILE_PATTERNS = (
    re.compile(r"(^|/)__pycache__(/|$)"),
    re.compile(r"\.pyc$"),
    re.compile(r"\.bak(?:[-.]|$)"),
)


ACTIVE_GENERAL_SKILLS = {
    "ad-keyword-analyzer",
    "conversion-copy-system",
    "customer-insight-feedback",
    "customer-research-synthesis",
    "customer-service",
    "discover-crossborder-market-trends",
    "decision-engine",
    "deep-analysis",
    "detail-page-replica-system",
    "ecommerce-copywriting",
    "ecommerce-visual-design",
    "growth-evidence-system",
    "reckon-ad-operator",
    "seo-geo-content",
    "team-service-orchestration",
    "visual-quality-gate",
    "xhs-reply-kb",
}


MCP_SERVERS = [
    {
        "name": "at_sellersprite",
        "display_name": "Agent Team · SellerSprite",
        "description": "Amazon 市场、关键词、ASIN、评论与趋势数据（只读）。",
        "bucket": "Agent Team 数据工具",
        "connection": {
            "transport": "streamable_http",
            "url": "https://mcp.sellersprite.com/mcp",
            "headers": {"secret-key": "${secret.AGENT_TEAM_SELLERSPRITE_MCP_KEY}"},
        },
        "tool_names": [
            "asin_detail",
            "asin_prediction",
            "competitor_lookup",
            "google_trend",
            "keepa_info",
            "keyword_order",
            "keyword_research",
            "market_brand_concentration",
            "market_price_distribution",
            "market_product_concentration",
            "market_research",
            "market_research_statistics",
            "market_seller_type_concentration",
            "product_research",
            "review",
            "traffic_keyword",
        ],
    },
    {
        "name": "at_sorftime",
        "display_name": "Agent Team · Sorftime",
        "description": "Amazon、Walmart、TikTok 与 1688 跨平台数据（只读）。",
        "bucket": "Agent Team 数据工具",
        "connection": {
            "transport": "streamable_http",
            "url": "https://mcp.sorftime.com?key=${secret.AGENT_TEAM_SORFTIME_MCP_KEY}",
            "headers": {},
        },
        "tool_names": [
            "ali1688_product_request",
            "ali1688_product_search",
            "ali1688_similar_product",
            "category_report",
            "keyword_detail",
            "keyword_trend",
            "product_customers_say",
            "product_detail",
            "product_reviews",
            "product_search",
            "product_search_from_history",
            "product_traffic_terms",
            "product_trend",
            "tiktok_product_detail",
            "tiktok_product_trend",
            "tiktok_product_video_author",
            "walmart_product_detail_by_product_id",
            "walmart_product_traffic_terms",
            "walmart_product_trend_by_product_id",
        ],
    },
]


AGENT_TOOL_BINDINGS = {
    "orange-pm": [],
    "cc-amz": [
        *(f"at_sellersprite.{name}" for name in MCP_SERVERS[0]["tool_names"]),
        *(f"at_sorftime.{name}" for name in MCP_SERVERS[1]["tool_names"]),
    ],
    "cc-copy": [
        "at_sellersprite.keyword_research",
        "at_sellersprite.review",
        "at_sellersprite.traffic_keyword",
        "at_sorftime.keyword_detail",
        "at_sorftime.keyword_trend",
        "at_sorftime.product_customers_say",
        "at_sorftime.product_reviews",
    ],
    "cc-ads": [
        "at_sellersprite.asin_detail",
        "at_sellersprite.competitor_lookup",
        "at_sellersprite.keyword_order",
        "at_sellersprite.keyword_research",
        "at_sellersprite.market_price_distribution",
        "at_sellersprite.traffic_keyword",
        "at_sorftime.keyword_detail",
        "at_sorftime.keyword_trend",
        "at_sorftime.product_traffic_terms",
    ],
    "cc-cs": [
        "at_sellersprite.review",
        "at_sorftime.product_customers_say",
        "at_sorftime.product_reviews",
    ],
    "cc-art": [
        "at_sellersprite.asin_detail",
        "at_sorftime.product_detail",
    ],
    "researcher": [
        "at_sellersprite.google_trend",
        "at_sorftime.category_report",
        "at_sorftime.product_search",
        "at_sorftime.product_trend",
    ],
    "crossborder-trend-researcher": [
        "at_sellersprite.google_trend",
        "at_sellersprite.keyword_research",
        "at_sellersprite.market_research",
        "at_sellersprite.market_research_statistics",
        "at_sellersprite.market_brand_concentration",
        "at_sellersprite.market_product_concentration",
        "at_sellersprite.market_price_distribution",
        "at_sellersprite.product_research",
        "at_sorftime.category_report",
        "at_sorftime.keyword_detail",
        "at_sorftime.keyword_trend",
        "at_sorftime.product_search",
        "at_sorftime.product_search_from_history",
        "at_sorftime.product_trend",
        "at_sorftime.tiktok_product_trend",
        "at_sorftime.walmart_product_trend_by_product_id",
        "at_sorftime.ali1688_product_search",
    ],
}

AMAZON_RESEARCH_TOOL_NAMES = tuple(AGENT_TOOL_BINDINGS["cc-amz"])


def _node(
    node_id: str,
    name: str,
    instruction: str,
    *,
    node_type: str = "collect_info",
    expected: list[str] | None = None,
    actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "type": node_type,
        "name": name,
        "instruction": instruction,
        "expected_user_info": expected or [],
        "allowed_actions": actions or ["continue_flow"],
    }


def _linear_sop(
    source_id: str,
    name: str,
    description: str,
    nodes: list[dict[str, Any]],
    *,
    triggers: list[str],
    goals: list[str],
    optional_defaults: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "skill_id": f"agent_team_{source_id.replace('-', '_')}",
        "version": MIGRATION_VERSION,
        "name": name,
        "business_domain": "跨境电商",
        "description": description,
        "trigger_intents": triggers,
        "user_utterance_examples": triggers,
        "goal": goals,
        "required_info": sorted(
            {field for node in nodes for field in node.get("expected_user_info", [])}
        ),
        "slot_filling_policy": {
            "enabled": True,
            "multi_slot_per_turn": True,
            "extract_scope": "all_skill_expected_user_info",
            "skip_satisfied_steps": True,
            "ask_only_for_required_info": True,
            "optional_info_policy": "assume_and_disclose",
            "direct_delivery_policy": "do_not_ask_optional_questions",
            "optional_defaults": optional_defaults or {},
        },
        "response_rules": [
            "区分已确认事实、推断和数据缺口。",
            "外部数据必须来自工具结果，不得根据记忆编造。",
            "写操作和对外发送必须再次取得用户确认。",
            "任务对象和目标明确时，默认先交付可用首版；非 required_info 缺失时采用约定默认值或合理假设并显式标注。",
            "用户明确说直接做、先出一版或不要补充提问时，除真正阻塞字段和不可逆操作确认外不得重复追问。",
        ],
        "nodes": nodes,
        "edges": [
            {"source_node_id": nodes[index]["node_id"], "next_node_id": nodes[index + 1]["node_id"]}
            for index in range(len(nodes) - 1)
        ],
        "start_node_id": nodes[0]["node_id"],
        "terminal_node_ids": [nodes[-1]["node_id"]],
        "interruption_policy": {
            "unrelated_request": "pause_and_answer",
            "resume": "continue_from_last_incomplete_node",
        },
    }


def _amazon_research_sop() -> dict[str, Any]:
    tool_actions = ["continue_flow", *(f"call_tool:{name}" for name in AMAZON_RESEARCH_TOOL_NAMES)]
    nodes = [
        _node(
            "collect_scope",
            "确认研究范围",
            "识别产品、ASIN 或关键词。站点、深度和决策问题均为非关键信息；缺失时使用美国站、L2 和是否值得做，不得追问。",
            expected=["product_or_asin"],
        ),
        {
            **_node(
                "research_l1",
                "L1 快速判断",
                "调用最少的 SellerSprite、Keepa 或 Sorftime 工具取得一项足以支撑判断的真实证据后立即停止；总调用建议不超过 3 次。",
                node_type="tool_call",
                actions=tool_actions,
            ),
            "metadata": {"evidence_policy": {"min_successful_tools": 1}},
        },
        {
            **_node(
                "research_l2",
                "L2 标准研究",
                "围绕市场、竞争、价格、痛点、流量和门槛按需选择 2—4 个维度；SellerSprite 为主，历史或评论不足时再用 Keepa/Sorftime，证据够用即停。",
                node_type="tool_call",
                actions=tool_actions,
            ),
            "metadata": {"evidence_policy": {"min_successful_tools": 2}},
        },
        {
            **_node(
                "research_l3",
                "L3 完整选品",
                "覆盖与决策相关的完整维度，至少使用两个数据源交叉验证；允许调用 SellerSprite 15 次、Keepa 6 次、Sorftime 10 次以内，缺失维度直接标注而非反复试错。",
                node_type="tool_call",
                actions=tool_actions,
            ),
            "metadata": {
                "evidence_policy": {"min_successful_tools": 4, "min_tool_families": 2}
            },
        },
        _node(
            "evidence_gate",
            "证据检查",
            "区分事实、推断和缺口；逐项标注来源、数据日期及口径。不得把工具失败、空结果或模型记忆写成事实。",
        ),
        _node(
            "reply",
            "输出 EvidencePack",
            "先给做/不做/谨慎做，再给关键证据、竞品或关键词机会、风险、数据缺口和下一步。L1 保持简短，L2 使用标准 EvidencePack，L3 给完整决策报告。",
            node_type="response",
            actions=["answer_user"],
        ),
    ]
    content = _linear_sop(
        "amazon-research",
        "Amazon 选品与竞品研究",
        "按 L1/L2/L3 意图分档执行；只在任务需要可审计外部证据时进入，证据够用即止。",
        nodes,
        triggers=[
            "这个 ASIN 怎么样",
            "做一份竞品分析",
            "这个品类值不值得做",
            "做完整选品报告",
            "去 1688 找同款",
            "对比 Walmart 或 TikTok 趋势",
        ],
        goals=["获得可追溯市场证据", "形成选品或竞品判断", "输出标准 EvidencePack"],
        optional_defaults={
            "marketplace": "Amazon 美国站",
            "research_depth": "L2",
            "decision_question": "是否值得做以及主要风险",
        },
    )
    content["edges"] = [
        {
            "source_node_id": "collect_scope",
            "next_node_id": "research_l1",
            "predicate_json": {"slot": "research_depth", "op": "eq", "value": "L1"},
            "priority": 0,
        },
        {
            "source_node_id": "collect_scope",
            "next_node_id": "research_l3",
            "predicate_json": {"slot": "research_depth", "op": "eq", "value": "L3"},
            "priority": 1,
        },
        {
            "source_node_id": "collect_scope",
            "next_node_id": "research_l2",
            "condition": "default",
            "priority": 2,
        },
        {"source_node_id": "research_l1", "next_node_id": "evidence_gate"},
        {"source_node_id": "research_l2", "next_node_id": "evidence_gate"},
        {"source_node_id": "research_l3", "next_node_id": "evidence_gate"},
        {"source_node_id": "evidence_gate", "next_node_id": "reply"},
    ]
    return content


SOP_TEMPLATES = {
    "orange-pm": _linear_sop(
        "orange-routing",
        "橙橙 · 需求判断与任务编排",
        "识别主责员工、输入依赖、交接物和质量门；当前版本不自动跨员工派单。",
        [
            _node(
                "collect_goal",
                "收集目标",
                "确认任务目标；交付物默认采用可执行方案，背景、截止时间和已有材料均为非关键信息，缺失时使用当前上下文和通用质量门直接出首版，不得追问。",
                expected=["goal"],
            ),
            _node(
                "classify_owner",
                "判断主责",
                "根据证据、文案、广告、客服、视觉或研究类型确定主责与支持角色。",
            ),
            _node(
                "design_handoffs",
                "设计交接",
                "列出执行顺序、每一步输入、输出和质量门；不得声称已经调用其他员工。",
            ),
            _node("quality_gate", "质量检查", "检查证据缺口、角色冲突、外部写操作和最终验收口径。"),
            _node(
                "reply",
                "输出编排方案",
                "向用户给出可执行的员工分工和下一步。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=["这个任务该派给谁", "帮我拆解跨境电商任务", "运营和广告结论冲突怎么办"],
        goals=["明确主责员工", "明确交接物与执行顺序", "建立最终质量门"],
        optional_defaults={
            "deliverable": "可执行的员工分工与任务方案",
            "deadline": "未指定，不作为首版交付阻塞项",
            "materials": "使用当前会话已有材料",
        },
    ),
    "cc-amz": _amazon_research_sop(),
    "cc-copy": _linear_sop(
        "conversion-copy",
        "小宁 · 转化文案生产",
        "基于证据、人群和平台约束生产可用文案，并进行转化与合规检查。",
        [
            _node(
                "collect_brief",
                "收集文案 Brief",
                "确认产品；平台默认 Amazon，目标人群默认该品类核心购买者，转化目标默认提升点击与购买，语气默认清晰可信。以上除产品外均为非关键信息，缺失时采用默认值并标注，不得追问。",
                expected=["product"],
            ),
            _node(
                "evidence_gate",
                "检查证据",
                "区分可证明卖点、用户原话和待验证假设；不得补写虚假数据。",
            ),
            _node("draft", "生成文案", "按平台结构生成标题、正文、卖点、CTA 或 Listing 模块。"),
            _node(
                "review",
                "转化与合规复核",
                "检查卖点优先级、关键词自然度、重复、绝对化承诺和平台限制。",
            ),
            _node(
                "reply",
                "交付 CopyBrief",
                "交付成稿、采用的证据、风险和可测试变体。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=["写 Amazon Listing", "优化商品文案", "把用户洞察转成卖点"],
        goals=["形成可直接使用的转化文案", "保留证据依据", "通过平台与合规检查"],
        optional_defaults={
            "platform": "Amazon",
            "audience": "该品类核心购买者",
            "conversion_goal": "提升点击与购买转化",
            "tone": "清晰、可信、不过度承诺",
        },
    ),
    "cc-ads": _linear_sop(
        "ads-diagnosis",
        "小辰 · Amazon 广告诊断",
        "基于广告指标、关键词和竞品证据形成诊断与操作建议，不自动修改广告。",
        [
            _node(
                "collect_metrics",
                "收集广告数据",
                "确认 ASIN；站点默认美国站，时间窗默认近 30 天，预算、广告指标和目标均为非关键信息，缺失时按现有市场数据先输出估算版并标注假设，不得追问。",
                expected=["asin"],
            ),
            _node(
                "fetch_context",
                "补充市场上下文",
                "按需查询关键词、流量词、竞品价格和 ASIN 详情。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.keyword_research",
                    "call_tool:at_sellersprite.traffic_keyword",
                    "call_tool:at_sellersprite.asin_detail",
                ],
            ),
            _node(
                "diagnose",
                "诊断漏斗",
                "按 CTR、CVR、CPC、ACoS/ROAS 分层定位词、素材、详情页、价格或预算问题。",
            ),
            _node(
                "decision",
                "形成操作方案",
                "给出保留、降价、否词、调价、加预算或暂停建议；明确判断阈值和验证窗口。",
            ),
            _node(
                "reply",
                "输出 GrowthPlan",
                "交付优先级、建议动作、预期信号和风险；不得声称已实际改动广告。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=["广告 ACoS 太高怎么办", "分析 Amazon 广告", "给我关键词投放建议"],
        goals=["定位广告漏斗问题", "形成有阈值的操作建议", "定义复盘窗口"],
        optional_defaults={
            "marketplace": "Amazon 美国站",
            "date_range": "近 30 天",
            "ad_metrics": "缺失时使用市场信号形成估算版",
            "target": "优先改善有效流量与可控 ACoS",
        },
    ),
    "cc-cs": _linear_sop(
        "customer-voc",
        "小月 · 客服与 VOC 分析",
        "从咨询、评论和售后记录中提炼问题、风险和可回流的产品/文案建议。",
        [
            _node(
                "collect_feedback",
                "收集反馈",
                "优先使用用户已贴出的评论、咨询或工单；平台、产品、时间范围和分析目标均为非关键信息。没有样本时先交付 VOC 分析框架、风险假设和建议的数据清单，不得追问。",
                expected=[],
            ),
            _node(
                "fetch_reviews",
                "按需补评论",
                "用户允许且有产品标识时，可补充只读评论数据。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.review",
                    "call_tool:at_sorftime.product_reviews",
                ],
            ),
            _node(
                "cluster", "聚类与分级", "按问题主题、频次、严重性、产品缺陷、误解和服务问题聚类。"
            ),
            _node("handoff", "形成回流建议", "分别给运营、文案、产品和客服口径输出可执行建议。"),
            _node(
                "reply",
                "输出 CustomerInsightPack",
                "交付事实、样本、趋势、风险和建议；不自动回复平台用户。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=["分析客户差评", "整理客服问题", "做一份 VOC 报告"],
        goals=["识别高频与高风险问题", "形成跨角色回流建议", "沉淀客服口径"],
        optional_defaults={
            "platform": "当前会话所指平台，无法判断时按 Amazon",
            "date_range": "近 90 天",
            "analysis_goal": "识别高频问题、严重风险和跨角色改进项",
            "feedback_samples": "缺失时交付分析框架并标注待验证",
        },
    ),
    "cc-art": _linear_sop(
        "visual-brief",
        "小菲 · 电商视觉 Brief 与质检",
        "把产品证据和文案转成主图/A+/详情页/视频 Brief；当前首批不自动调用生图与发送。",
        [
            _node(
                "collect_assets",
                "收集视觉输入",
                "优先使用当前会话中的产品、素材和目标；平台默认 Amazon，类型未说明时默认主图与 A+ 核心模块。尺寸、产品图、品牌规范、文案和参考风格均为非关键信息，缺失时用占位说明和素材待办交付 VisualBrief，不得追问，也不得假装已出图。",
                expected=[],
            ),
            _node(
                "inspect_product",
                "核对产品信息",
                "按需查询商品详情，核对外观、规格和真实卖点。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.asin_detail",
                    "call_tool:at_sorftime.product_detail",
                ],
            ),
            _node(
                "design_brief",
                "设计 VisualBrief",
                "定义每张图/镜头的目标、构图、信息层级、文案、素材和一致性约束。",
            ),
            _node(
                "quality_gate",
                "视觉质检",
                "检查产品一致性、平台规范、可读性、信息密度、夸大表达和素材授权。",
            ),
            _node(
                "reply",
                "交付 VisualBrief",
                "交付可生产的视觉清单与质检结论；未调用生成工具时不得声称已出图。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=["做 Amazon 主图方案", "设计 A+ 页面", "给我电商视频分镜"],
        goals=["形成可执行视觉 Brief", "保证产品与品牌一致性", "通过平台质量门"],
        optional_defaults={
            "platform": "Amazon",
            "asset_type": "主图与 A+ 核心模块",
            "product_assets": "缺失时使用明确占位和素材待办",
            "copy_brief": "根据已知卖点形成待确认文案层级",
        },
    ),
    "crossborder-trend-researcher": _linear_sop(
        "crossborder-market-trends",
        "跨境品类风口研究 · 多渠道机会发现",
        "从中文父品类建立候选池，用长期趋势、当前供需和跨渠道证据筛选增长细分，并交付可审计的 HTML/XLSX 报告。",
        [
            _node(
                "collect_scope",
                "确认父品类与默认范围",
                "确认中文父品类；目标市场、英文边界、研究周期、季节窗口、禁售约束和可用账号均为非关键信息。市场缺失时默认 Amazon 美国站，周期默认最近四个完整历史年份加当年可比周期，其余未知项标注暂无/不清楚后直接开始，不得追问。",
                expected=["parent_category"],
            ),
            _node(
                "channel_roadmap",
                "制定渠道路线图",
                "说明外部趋势、目标电商平台、第三方电商数据、评论社区、TikTok/Walmart/1688 分别回答什么问题，按当前已绑定只读工具直接推进；只有真实遇到登录、订阅或最小导出阻塞时才暂停，并且每次只给用户一个操作。",
            ),
            _node(
                "candidate_pool",
                "建立细分类目候选池",
                "从父品类出发，结合至少三类发现信号生成 10—30 个候选细分类目；先求覆盖，再排除明显不相关项，不围绕用户随口提到的单一细分倒推结论。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.market_research",
                    "call_tool:at_sellersprite.product_research",
                    "call_tool:at_sorftime.category_report",
                    "call_tool:at_sorftime.product_search",
                ],
            ),
            _node(
                "keyword_map",
                "构建双语关键词簇",
                "为候选整理产品形态、功能结果、材料/技术、人群/痛点、场景/季节五类双语关键词；每个英文词附中文翻译，品牌、规格和价格只作辅助标签。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.keyword_research",
                    "call_tool:at_sorftime.keyword_detail",
                ],
            ),
            _node(
                "collect_evidence",
                "采集可追溯原始证据",
                "按候选采集市场、关键词、商品、价格、品牌集中度和跨平台信号；每个数值保留来源、单位、市场、时间窗、采集日期和 URL。工具空结果必须标数据缺口，禁止用模型记忆补数。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.market_research_statistics",
                    "call_tool:at_sellersprite.market_brand_concentration",
                    "call_tool:at_sellersprite.market_product_concentration",
                    "call_tool:at_sellersprite.market_price_distribution",
                    "call_tool:at_sorftime.product_search_from_history",
                ],
            ),
            _node(
                "long_term_trend",
                "验证长期趋势",
                "使用至少三个完整历史年份检验方向、正增长频率、增速、季节性、区域扩散和关键词簇扩张；未完整当年不进入完整年度 CAGR，只与往年相同月份或季节窗口比较。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.google_trend",
                    "call_tool:at_sorftime.keyword_trend",
                    "call_tool:at_sorftime.product_trend",
                ],
            ),
            _node(
                "supply_demand",
                "分析当期供需",
                "仅在同一来源、市场、时间窗和定义内比较需求增长与供给增长，识别需求快于供给的候选；不得跨源相加绝对值，不得把 CPC、播放量或商品数解释为销量。",
            ),
            _node(
                "cross_channel",
                "验证跨渠道共振与反证",
                "用 Amazon、外部趋势、TikTok、Walmart、1688 等相互独立信号检查方向一致和因果逻辑，并主动寻找单一品牌、爆款或事件驱动等反证。跨渠道共振不表示数值可比。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sorftime.tiktok_product_trend",
                    "call_tool:at_sorftime.walmart_product_trend_by_product_id",
                    "call_tool:at_sorftime.ali1688_product_search",
                ],
            ),
            _node(
                "score_decide",
                "评分与决策门",
                "基于长期增长、当前需求、供需差、跨渠道共振、品牌进入空间和证据完整度评分；检查专利、设计买断、模具、认证、MOQ、物流等高成本壁垒。关键证据不足时标数据不足，不强行排名。",
            ),
            _node(
                "deliver_report",
                "交付 HTML/XLSX 研究报告",
                "先交付候选机会、证据、反证、风险、下一步验证、通过标准和停止条件，并区分事实、计算、推断与假设。用户要求 HTML 时由平台发布并追加真实公网链接；用户要求 XLSX 时按技能工作簿契约生成真实附件或下载链接。链接或附件返回前不得声称已生成。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=[
            "帮我找这个品类的海外风口",
            "从父品类研究增长细分",
            "做跨境品类机会研究",
            "分析这个品类的趋势和供需",
        ],
        goals=[
            "建立可审计的细分类目候选池",
            "验证长期趋势、当前供需与跨渠道共振",
            "形成带反证和决策门的 HTML/XLSX 报告",
        ],
        optional_defaults={
            "target_market": "Amazon 美国站",
            "english_boundary": "由父品类提出首版英文边界并标注",
            "time_window": "最近四个完整历史年份加当年可比周期",
            "seasonal_window": "根据品类提出首版季节窗口",
            "constraints": "未知项标注暂无/不清楚，候选出现后再核查高成本壁垒",
        },
    ),
    "researcher": _linear_sop(
        "fact-research",
        "研究员 · 事实核查与研究简报",
        "将问题拆成可核查命题，收集有日期和出处的证据并输出 ResearchBrief。",
        [
            _node(
                "collect_question",
                "明确研究问题",
                "确认研究问题；范围默认覆盖与问题直接相关的市场，地区默认当前业务语境，时间口径默认最新可获得数据。以上除研究问题外均为非关键信息，缺失时采用默认值并标注，不得追问。",
                expected=["research_question"],
            ),
            _node(
                "research",
                "收集证据",
                "使用当前已绑定的数据工具或用户材料；没有来源时明确数据缺口。",
                node_type="tool_call",
                actions=[
                    "continue_flow",
                    "call_tool:at_sellersprite.google_trend",
                    "call_tool:at_sorftime.product_search",
                    "call_tool:at_sorftime.category_report",
                ],
            ),
            _node(
                "cross_check", "交叉核查", "对关键结论至少核对两个独立信号；记录来源日期和冲突。"
            ),
            _node(
                "reply",
                "输出 ResearchBrief",
                "先列最关键发现，再列证据、分歧、不确定性和下一步。",
                node_type="response",
                actions=["answer_user"],
            ),
        ],
        triggers=["帮我调研这个市场", "核查这个说法", "做一份研究简报"],
        goals=["形成可追溯研究结论", "显式标记不确定性", "提供下一步核查路径"],
        optional_defaults={
            "scope": "与问题直接相关的市场和关键变量",
            "region": "当前业务语境，无法判断时使用全球/美国市场",
            "time_basis": "最新可获得数据",
            "sources": "当前绑定工具与用户材料",
        },
    ),
}


# StaffDeck chats are opened against one employee and currently cannot hand work to
# another employee automatically. Bind the team's read-only Amazon research flow to
# every migrated employee so an ASIN research request can still use real data instead
# of falling back to an answer-only turn. Each employee keeps their own primary SOP.
SHARED_AMAZON_RESEARCH_SKILL_ID = SOP_TEMPLATES["cc-amz"]["skill_id"]
SHARED_AMAZON_RESEARCH_TOOLS = (
    "at_sellersprite.asin_detail",
    "at_sellersprite.keyword_research",
    "at_sellersprite.market_research",
    "at_sellersprite.keepa_info",
    "at_sorftime.product_trend",
    "at_sorftime.product_reviews",
)


@dataclass(frozen=True)
class SourceCard:
    source_id: str
    card_path: Path
    persona_path: Path
    card: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def _all_cards(source_root: Path) -> dict[str, SourceCard]:
    cards: dict[str, SourceCard] = {}
    for card_path in sorted((source_root / "registry").glob("**/card.yaml")):
        card = _load_yaml(card_path)
        source_id = str(card.get("id") or "").strip()
        if not source_id:
            continue
        persona_path = card_path.with_name("persona.md")
        if not persona_path.exists():
            raise ValueError(f"Persona is missing for {source_id}: {persona_path}")
        cards[source_id] = SourceCard(source_id, card_path, persona_path, card)
    return cards


def _resolve_skill_dir(source_root: Path, source_card: SourceCard, skill_name: str) -> Path:
    candidates = [source_root / "base" / ".claude" / "skills" / skill_name]
    candidates.append(source_card.card_path.parent / "skills" / skill_name)
    for parent in source_card.card_path.parents:
        if parent.name == "packs":
            break
        if (parent / "pack.yaml").exists():
            candidates.append(parent / "skills" / skill_name)
            break
    candidates.append(source_root / "registry" / "kernel" / "skills" / skill_name)
    for candidate in candidates:
        if (candidate / "SKILL.md").exists():
            return candidate
    raise FileNotFoundError(
        f"Cannot resolve canonical skill {skill_name} for {source_card.source_id}"
    )


def _safe_text(text: str, sensitive_values: set[str] | None = None) -> str:
    for value in sorted(sensitive_values or set(), key=len, reverse=True):
        if value:
            text = text.replace(value, "__REDACTED_USE_AGENT_TEAM_SECRET__")
    text = SECRET_JSON_PATTERN.sub(r'\1"__REDACTED_USE_AGENT_TEAM_SECRET__"', text)
    text = SECRET_ENV_DEFAULT_PATTERN.sub(r'\1""\2', text)
    text = SECRET_ASSIGNMENT_PATTERN.sub(r'\1""', text)
    text = LEGACY_PATH_PATTERN.sub("$SKILL_WORKSPACE", text)
    text = text.replace(
        "mcp__studio__studio_report",
        "平台 HTML 成品发布能力（仅在用户明确要求时生成并附上真实公网链接）",
    )
    text = text.replace("$TASK_DIR", "当前会话工作目录")
    return text


def _sanitize_persona(text: str, source_id: str, sensitive_values: set[str] | None = None) -> str:
    compatibility = (
        "# Agent Team 兼容层\n\n"
        f"你是从 Agent Team 迁移来的独立数字员工 `{source_id}`。"
        "仅使用 Agent Team 当前绑定并显示可用的 SOP、通用技能、知识库和工具。"
        "Agent Team 当前不支持跨员工自动派单；不得声称已调用、等待或收到其他员工结果。"
        "需要其他角色协作时，输出清晰的交接 Brief，由用户选择下一位员工。"
        "外部数据必须来自本轮真实工具结果；写操作或对外发送必须先取得用户确认。"
        "任务对象和目标已经明确、能够形成有用结果时，默认先交付可用首版。"
        "只有缺少任务对象或目标会导致无法形成任何有效内容，或涉及付款、发布、删除、发送等不可逆操作确认时，才允许追问。"
        "其他非关键缺口必须使用合理默认值或假设并明确标注；用户说直接做、先出一版或不要补充提问时不得重复追问。"
        "当用户明确要求 HTML 成品时，必须用现有信息直接形成完整报告；非关键数据缺失时采用并标注默认值或估算，不得反复追问。"
        "HTML 由平台发布并追加真实公网链接；看到链接前不得声称已发送、已上传或已生成文件。\n\n"
    )
    return compatibility + _safe_text(text, sensitive_values).lstrip()


def _sanitized_skill_files(
    skill_dir: Path, sensitive_values: set[str] | None = None
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in skill_dir.glob("**/*") if item.is_file()):
        relative = path.relative_to(skill_dir).as_posix()
        if any(pattern.search(relative) for pattern in SKIPPED_FILE_PATTERNS):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        cleaned = _safe_text(content, sensitive_values)
        files.append(
            {
                "path": relative,
                "content": cleaned,
                "size": len(cleaned.encode("utf-8")),
                "mime_type": "text/markdown" if relative.endswith(".md") else "text/plain",
            }
        )
    if not any(item["path"] == "SKILL.md" for item in files):
        raise ValueError(f"Sanitized package has no SKILL.md: {skill_dir}")
    return files


def _legacy_or_secret_hits(value: Any) -> list[str]:
    serialized = json.dumps(value, ensure_ascii=False)
    markers = [
        "/opt/cc-base",
        "$HOME/.claude",
        "~/.claude",
        "mcp__studio__studio_report",
        "$TASK_DIR",
    ]
    return [marker for marker in markers if marker in serialized]


def _source_sensitive_values(source_root: Path) -> set[str]:
    markers = ("key", "secret", "token", "password")
    values: set[str] = set()

    def collect(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, str(key))
        elif isinstance(value, list):
            for item in value:
                collect(item, parent_key)
        elif (
            isinstance(value, str)
            and len(value) >= 8
            and any(marker in parent_key.lower() for marker in markers)
        ):
            values.add(value)

    for config_path in (source_root / "base" / ".claude" / "skills").glob("**/config.json"):
        try:
            collect(json.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return values


def compile_manifest(source_root: Path, team_name: str = "demo-ecom") -> dict[str, Any]:
    source_root = source_root.resolve()
    sensitive_values = _source_sensitive_values(source_root)
    team_path = source_root / "registry" / "teams" / f"{team_name}.yaml"
    team = _load_yaml(team_path)
    source_ids = [str(team["lead"]), *(str(item) for item in team.get("members", []))]
    cards = _all_cards(source_root)
    missing = [source_id for source_id in source_ids if source_id not in cards]
    if missing:
        raise ValueError(f"Team references missing cards: {', '.join(missing)}")

    agents: list[dict[str, Any]] = []
    skill_owners: dict[str, set[str]] = {}
    skill_dirs: dict[str, Path] = {}
    for source_id in source_ids:
        source_card = cards[source_id]
        card = source_card.card
        required_skills = [str(item) for item in card.get("requires_skills", [])]
        for skill_name in required_skills:
            skill_owners.setdefault(skill_name, set()).add(source_id)
            skill_dirs.setdefault(
                skill_name, _resolve_skill_dir(source_root, source_card, skill_name)
            )
        metadata = {
            "migration_source": MIGRATION_SOURCE,
            "migration_version": MIGRATION_VERSION,
            "source_team": team_name,
            "source_agent_id": source_id,
            "source_version": str(card.get("version") or ""),
            "role_name": str(card.get("label") or ""),
            "role_kind": str(card.get("role_kind") or ""),
            "team": str(team.get("pack") or team_name),
            "work_styles": [str(card.get("tagline") or "")],
            "expertise_tags": [
                str(tag)
                for capability in card.get("capabilities", [])
                if isinstance(capability, dict)
                for tag in capability.get("tags", [])
            ],
            "agent_team_capabilities": card.get("capabilities", []),
            "agent_team_delivers": card.get("delivers", []),
            "agent_team_consumes": card.get("consumes", []),
        }
        agents.append(
            {
                "source_id": source_id,
                "name": str(card.get("name") or source_id),
                "description": str(card.get("label") or card.get("tagline") or source_id),
                "persona_prompt": _sanitize_persona(
                    source_card.persona_path.read_text(encoding="utf-8"),
                    source_id,
                    sensitive_values,
                ),
                "metadata": metadata,
                "general_skill_slugs": [f"agent-team-{name}" for name in required_skills],
                "sop_skill_ids": list(
                    dict.fromkeys(
                        [
                            *(
                                [SOP_TEMPLATES[source_id]["skill_id"]]
                                if source_id in SOP_TEMPLATES
                                else []
                            ),
                            SHARED_AMAZON_RESEARCH_SKILL_ID,
                        ]
                    )
                ),
                "tool_names": list(
                    dict.fromkeys(
                        [
                            *AGENT_TOOL_BINDINGS.get(source_id, []),
                            *SHARED_AMAZON_RESEARCH_TOOLS,
                        ]
                    )
                ),
            }
        )

    general_skills = []
    for skill_name in sorted(skill_dirs):
        files = _sanitized_skill_files(skill_dirs[skill_name], sensitive_values)
        general_skills.append(
            {
                "source_name": skill_name,
                "slug": f"agent-team-{skill_name}",
                "name": f"Agent Team · {skill_name}",
                "description": f"从 Agent Team 迁移；原技能 {skill_name}。",
                "homepage": f"agent-team://{team_name}/skills/{skill_name}",
                "status": "published" if skill_name in ACTIVE_GENERAL_SKILLS else "draft",
                "owners": sorted(skill_owners[skill_name]),
                "files": files,
            }
        )

    sops = [
        {"source_agent_id": source_id, "status": "published", "content": SOP_TEMPLATES[source_id]}
        for source_id in source_ids
        if source_id in SOP_TEMPLATES
    ]
    manifest: dict[str, Any] = {
        "migration_source": MIGRATION_SOURCE,
        "migration_version": MIGRATION_VERSION,
        "tenant_id": TENANT_ID,
        "team": {
            "name": team_name,
            "pack": team.get("pack"),
            "lead": team.get("lead"),
            "members": team.get("members", []),
        },
        "agents": agents,
        "general_skills": general_skills,
        "sops": sops,
        "mcp_servers": MCP_SERVERS,
        "warnings": [
            "Agent Team 当前不支持跨员工自动派单；橙橙 SOP 仅输出编排方案。",
            "脚本型外部技能以 draft 导入，待转为 Agent Team Tool 后再发布。",
            "图片、视频、飞书和平台写操作尚未在首批启用。",
        ],
    }
    hits = _legacy_or_secret_hits(manifest)
    if hits:
        raise ValueError(f"Manifest still contains legacy runtime markers: {', '.join(hits)}")
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest["counts"] = {
        "agents": len(agents),
        "general_skills": len(general_skills),
        "published_general_skills": sum(item["status"] == "published" for item in general_skills),
        "sops": len(sops),
        "mcp_servers": len(MCP_SERVERS),
        "mcp_tools": sum(len(item["tool_names"]) for item in MCP_SERVERS),
    }
    return manifest


class ApiError(RuntimeError):
    pass


class StaffDeckApi:
    def __init__(self, base_url: str, tenant_id: str = TENANT_ID):
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.token = ""

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 180
    ) -> Any:
        headers: dict[str, str] = {}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ApiError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {path} failed: {exc.reason}") from exc

    def login(self, username: str, password: str) -> None:
        response = self.request(
            "POST",
            "/api/auth/login",
            {"tenant_id": self.tenant_id, "username": username, "password": password},
        )
        self.token = str(response["token"])

    def query_path(self, path: str, **params: str) -> str:
        query = urllib.parse.urlencode(params)
        return f"{path}?{query}"


def _index(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get(key)): row for row in rows if row.get(key) is not None}


def tool_update_payload(row: dict[str, Any], *, effect_level: str) -> dict[str, Any]:
    """Build a lossless ToolUpdateRequest payload for a discovered MCP tool."""
    return {
        "tenant_id": row["tenant_id"],
        "name": row["name"],
        "display_name": row.get("display_name"),
        "description": row.get("description"),
        "bucket": row.get("bucket") or "Agent Team 数据工具",
        "tool_type": row.get("tool_type") or "mcp",
        "method": row.get("method") or "POST",
        "url": row["url"],
        "headers": row.get("headers") or {},
        "auth": row.get("auth") or {},
        "mcp_config": row.get("mcp_config") or {},
        "input_schema": row.get("input_schema") or {},
        "output_schema": row.get("output_schema") or {},
        "allowed_skills": row.get("allowed_skills") or [],
        "effect_level": effect_level,
        "enabled": row.get("enabled") is not False,
    }


def _select_managed_agent(
    candidates: list[dict[str, Any]], desired_name: str
) -> dict[str, Any] | None:
    if not candidates:
        return None
    return next(
        (item for item in candidates if str(item.get("name")) == desired_name),
        candidates[0],
    )


def apply_manifest(api: StaffDeckApi, manifest: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(manifest["tenant_id"])
    report: dict[str, Any] = {"created": {}, "updated": {}, "warnings": list(manifest["warnings"])}

    agents = api.request("GET", api.query_path("/api/enterprise/agents", tenant_id=tenant_id))
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in agents:
        metadata = item.get("metadata", {})
        if metadata.get("migration_source") != MIGRATION_SOURCE:
            continue
        source_id = str(metadata.get("source_agent_id") or "")
        if source_id:
            by_source.setdefault(source_id, []).append(item)
    by_name = _index(agents, "name")
    agent_rows: dict[str, dict[str, Any]] = {}
    for agent in manifest["agents"]:
        source_id = str(agent["source_id"])
        candidates = by_source.get(source_id, [])
        current = _select_managed_agent(candidates, str(agent["name"]))
        if len(candidates) > 1:
            report["warnings"].append(
                f"Multiple managed agents share source id {source_id}; "
                f"updated {current['id']} and preserved the other records"
            )
        payload = {
            "tenant_id": tenant_id,
            "name": agent["name"],
            "description": agent["description"],
            "persona_prompt": agent["persona_prompt"],
            "metadata": agent["metadata"],
        }
        if current:
            row = api.request("PUT", f"/api/enterprise/agents/{current['id']}", payload)
            report["updated"].setdefault("agents", []).append(source_id)
        else:
            collision = by_name.get(str(agent["name"]))
            if collision:
                raise ApiError(
                    f"Agent name collision for {agent['name']}; existing row is not managed by this migration"
                )
            row = api.request("POST", "/api/enterprise/agents", {**payload, "source_mode": "blank"})
            report["created"].setdefault("agents", []).append(source_id)
        agent_rows[source_id] = row

    servers = api.request("GET", api.query_path("/api/enterprise/mcp-servers", tenant_id=tenant_id))
    servers_by_name = _index(servers, "name")
    for server in manifest["mcp_servers"]:
        payload = {
            "tenant_id": tenant_id,
            "name": server["name"],
            "display_name": server["display_name"],
            "description": server["description"],
            "bucket": server["bucket"],
            "connection": server["connection"],
            "enabled": True,
        }
        current = servers_by_name.get(str(server["name"]))
        if current:
            row = api.request("PUT", f"/api/enterprise/mcp-servers/{current['id']}", payload)
            report["updated"].setdefault("mcp_servers", []).append(server["name"])
        else:
            row = api.request("POST", "/api/enterprise/mcp-servers", payload)
            report["created"].setdefault("mcp_servers", []).append(server["name"])
        sync = api.request(
            "POST",
            f"/api/enterprise/mcp-servers/{row['id']}/sync",
            {"tenant_id": tenant_id, "tool_names": server["tool_names"]},
        )
        if not sync.get("success"):
            raise ApiError(f"MCP sync failed for {server['name']}: {sync.get('error')}")

    # MCP tools use POST transport even when their business effect is read-only. Mark the
    # imported data connectors explicitly so the supervised runtime does not conservatively
    # treat them as writes and require approval.
    discovered_tools = _index(
        api.request("GET", api.query_path("/api/enterprise/tools", tenant_id=tenant_id)), "name"
    )
    read_only_names = {
        f"{server['name']}.{leaf}"
        for server in manifest["mcp_servers"]
        for leaf in server["tool_names"]
    }
    for name in sorted(read_only_names):
        row = discovered_tools.get(name)
        if not row or row.get("effect_level") == "read":
            continue
        api.request(
            "PUT",
            f"/api/enterprise/tools/{row['id']}",
            tool_update_payload(row, effect_level="read"),
        )
        report["updated"].setdefault("tool_effect_levels", []).append(name)

    existing_general = api.request(
        "GET", api.query_path("/api/enterprise/general-skills", tenant_id=tenant_id)
    )
    general_by_slug = _index(existing_general, "slug")
    general_rows: dict[str, dict[str, Any]] = {}
    for skill in manifest["general_skills"]:
        payload = {
            "tenant_id": tenant_id,
            "name": skill["name"],
            "slug": skill["slug"],
            "description": skill["description"],
            "homepage": skill["homepage"],
            "files": skill["files"],
            "status": skill["status"],
        }
        current = general_by_slug.get(str(skill["slug"]))
        if current:
            payload["original_slug"] = skill["slug"]
            report["updated"].setdefault("general_skills", []).append(skill["slug"])
        else:
            report["created"].setdefault("general_skills", []).append(skill["slug"])
        general_rows[str(skill["slug"])] = api.request(
            "POST", "/api/enterprise/general-skills/import", payload
        )

    existing_sops = api.request(
        "GET", api.query_path("/api/enterprise/skills", tenant_id=tenant_id)
    )
    sop_by_skill_id = _index(existing_sops, "skill_id")
    sop_rows: dict[str, dict[str, Any]] = {}
    available_tools = _index(
        api.request("GET", api.query_path("/api/enterprise/tools", tenant_id=tenant_id)), "name"
    )
    for sop in manifest["sops"]:
        content = json.loads(json.dumps(sop["content"], ensure_ascii=False))
        for node in content["nodes"]:
            node["allowed_actions"] = [
                action
                for action in node.get("allowed_actions", [])
                if not action.startswith("call_tool:")
                or action.removeprefix("call_tool:") in available_tools
            ]
        payload = {"tenant_id": tenant_id, "content": content, "status": sop["status"]}
        current = sop_by_skill_id.get(str(content["skill_id"]))
        if current:
            row = api.request("PUT", f"/api/enterprise/skills/{content['skill_id']}", payload)
            report["updated"].setdefault("sops", []).append(content["skill_id"])
        else:
            row = api.request("POST", "/api/enterprise/skills", payload)
            report["created"].setdefault("sops", []).append(content["skill_id"])
        sop_rows[str(content["skill_id"])] = row

    models = api.request(
        "GET", api.query_path("/api/enterprise/model-configs", tenant_id=tenant_id)
    )
    default_models = [item for item in models if item.get("is_default") and item.get("enabled")]
    if len(default_models) != 1:
        raise ApiError("Expected exactly one enabled default RC model before agent binding")
    model_id = str(default_models[0]["id"])

    tool_rows = _index(
        api.request("GET", api.query_path("/api/enterprise/tools", tenant_id=tenant_id)), "name"
    )
    for agent in manifest["agents"]:
        source_id = str(agent["source_id"])
        row = agent_rows[source_id]
        current_bindings = api.request(
            "GET",
            api.query_path(f"/api/enterprise/agents/{row['id']}/resources", tenant_id=tenant_id),
        )
        preserved = [
            item
            for item in current_bindings
            if item.get("metadata", {}).get("migration_source") != MIGRATION_SOURCE
        ]
        desired: list[dict[str, Any]] = []
        for slug in agent["general_skill_slugs"]:
            resource = general_rows.get(str(slug))
            if resource and resource.get("status") == "published":
                desired.append({"resource_type": "general_skill", "resource_id": resource["id"]})
        for skill_id in agent["sop_skill_ids"]:
            resource = sop_rows.get(str(skill_id))
            if resource:
                desired.append({"resource_type": "skill", "resource_id": resource["id"]})
        for tool_name in agent["tool_names"]:
            resource = tool_rows.get(str(tool_name))
            if resource:
                desired.append({"resource_type": "tool", "resource_id": resource["id"]})
            else:
                report["warnings"].append(f"Tool was not discovered and was not bound: {tool_name}")
        migrated = [
            {
                **item,
                "status": "active",
                "metadata": {
                    "migration_source": MIGRATION_SOURCE,
                    "source_team": manifest["team"]["name"],
                    "source_agent_id": source_id,
                },
            }
            for item in desired
        ]
        resources = [
            {
                "resource_type": item["resource_type"],
                "resource_id": item["resource_id"],
                "status": item.get("status", "active"),
                "metadata": item.get("metadata", {}),
            }
            for item in preserved
        ] + migrated
        api.request(
            "PUT",
            f"/api/enterprise/agents/{row['id']}/resources",
            {"tenant_id": tenant_id, "resources": resources},
        )
        api.request(
            "PUT",
            f"/api/enterprise/agents/{row['id']}/models",
            {
                "tenant_id": tenant_id,
                "bindings": [
                    {"role": role, "model_config_id": model_id}
                    for role in ("default", "router", "step", "response", "general_skill")
                ],
            },
        )

    report["counts"] = manifest["counts"]
    report["manifest_sha256"] = manifest["manifest_sha256"]
    return report


def _credentials(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    admin = next(item for item in data["accounts"] if item["username"] == "admin")
    return str(admin["username"]), str(admin["password"])


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Agent Team capability config to the Agent Team console"
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--team", default="demo-ecom")
    parser.add_argument("--base-url", default="http://127.0.0.1:18173")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    manifest = compile_manifest(args.source_root, args.team)
    if not args.apply:
        if args.output:
            _write_json(args.output, manifest)
        print(
            json.dumps(
                {
                    "status": "dry-run",
                    **manifest["counts"],
                    "manifest_sha256": manifest["manifest_sha256"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not args.credential_file:
        parser.error("--credential-file is required with --apply")
    username, password = _credentials(args.credential_file)
    api = StaffDeckApi(args.base_url, str(manifest["tenant_id"]))
    api.login(username, password)
    report = apply_manifest(api, manifest)
    if args.output:
        _write_json(args.output, report)
    print(
        json.dumps(
            {
                "status": "applied",
                "counts": report["counts"],
                "manifest_sha256": report["manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
