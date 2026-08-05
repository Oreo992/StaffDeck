from __future__ import annotations

from typing import Any


TARGET_GENERAL_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "slug": "sellersprite",
        "name": "SellerSprite 亚马逊数据研究",
        "description": (
            "当任务需要 Amazon 市场、ASIN、关键词、类目集中度、价格带或评论数据时使用；"
            "普通问候、写作和无需外部数据的讨论不要加载。"
        ),
        "markdown": """# SellerSprite 亚马逊数据研究

## 适用范围

用于 Amazon 选品、竞品、ASIN、关键词、类目、价格带、集中度和评论研究。它提供研究方法，不扩大工具权限，也不代替 SOP；只调用当前会话实际提供的工具。

## 数据原则

- SellerSprite 是 Amazon 主数据源。不得编造数字、排名、趋势、评论、图片 URL 或工具成功。
- 工具返回的业务错误、空数据或口径不明不算有效证据；说明失败原因，必要时换数据源。
- 同一轮不要用近义关键词反复试探；相同 ASIN 或关键词已有结果时避免重复调用。
- 自主控制研究深度：简单问题用足以回答的最少调用，完整研究才扩大覆盖；证据够用即停止。

## 常用路径

- ASIN 快速核验：`at_sellersprite.asin_detail`，需要预测时再用 `at_sellersprite.asin_prediction`，需要评论证据时再用 `at_sellersprite.review`。
- 关键词研究：先用 `at_sellersprite.keyword_research`，再按需用 `at_sellersprite.traffic_keyword` 或 `at_sellersprite.keyword_order`。
- 市场与类目：用 `at_sellersprite.market_research`、`at_sellersprite.market_research_statistics`，再按问题选择品牌、商品、卖家类型集中度或价格分布工具。
- 竞品与历史：用 `at_sellersprite.competitor_lookup`、`at_sellersprite.product_research`；价格或 BSR 历史确有必要时使用 `at_sellersprite.keepa_info`。

## 输出要求

先给数据快照（来源、日期、站点和口径），再说明覆盖范围与缺口。明确区分工具事实、分析推断和未知信息；最后给业务含义、结论、风险和下一步。快速问题保持简短，不为凑模板扩写。""",
    },
    {
        "slug": "sorftime",
        "name": "Sorftime 跨平台补充研究",
        "description": (
            "当任务需要 Amazon 历史时段或原始评论，或需要 TikTok、Walmart、1688 数据时使用；"
            "Amazon 主数据优先使用 SellerSprite。"
        ),
        "markdown": """# Sorftime 跨平台补充研究

## 适用范围

用于补充 Amazon 历史搜索、类目、流量词和评论，也用于 TikTok、Walmart、1688 的直接研究。Amazon 主数据优先使用 SellerSprite；只有缺口或交叉验证价值明确时再补充 Sorftime。

## 数据原则

- 不得编造 TikTok、Walmart、1688 或 Amazon 数据，不把不同平台信号混成同一种需求。
- 读取错误信息后再调整参数；相同参数最多重试两次，空结果要记录为数据缺口。
- 自主选择最少相关调用，证据足够即停止。不要为了“完整”把所有平台都查一遍。
- 注意参数口径：Amazon 站点使用工具要求的 `amzSite`；Walmart 的 `productId` 不是 ASIN；TikTok 商品 ID 是平台商品标识；历史查询按工具要求提供时间。

## 常用路径

- Amazon 补充：`at_sorftime.product_search`、`at_sorftime.product_search_from_history`、`at_sorftime.product_detail`、`at_sorftime.product_trend`。
- 关键词与评论：`at_sorftime.keyword_detail`、`at_sorftime.keyword_trend`、`at_sorftime.product_traffic_terms`、`at_sorftime.product_reviews`、`at_sorftime.product_customers_say`。
- TikTok：按需使用 `at_sorftime.tiktok_product_detail`、`at_sorftime.tiktok_product_trend`、`at_sorftime.tiktok_product_video_author`。
- Walmart：按需使用 `at_sorftime.walmart_product_detail_by_product_id`、`at_sorftime.walmart_product_trend_by_product_id`、`at_sorftime.walmart_product_traffic_terms`。
- 1688：用 `at_sorftime.ali1688_product_search` 找货源，再按需查询详情或相似商品。

## 输出要求

逐个平台标注来源、日期、站点和对象 ID。先陈述各平台事实，再说明可比性限制和推断；最后给对 Amazon 决策的补充价值。跨平台证据冲突时保留冲突，不强行合并。""",
    },
    {
        "slug": "deep-analysis",
        "name": "Amazon 完整深度分析",
        "description": (
            "仅当用户明确要求完整、全面、深度的选品或竞品报告，或需要多 ASIN 决策报告时使用；"
            "快速问题和普通单点分析不要加载。"
        ),
        "markdown": """# Amazon 完整深度分析

## 触发条件

仅用于用户明确要求“完整/全面/深度报告”、多 ASIN 竞品全景，或正式进场决策。快速查询、单个指标或探索性讨论不要加载。该技能组织分析维度，不强制无关维度，不扩大工具权限，也不能把缺失数据补写成事实。

## 分析框架

根据用户目标覆盖相关模块，并说明未覆盖原因：

1. 竞品基本面：核心 ASIN、价格、销量或排名口径、上架时间和定位。
2. SKU 与变体：规格、颜色、套装、父子体和主要流量承接关系。
3. 目标人群与场景：从关键词、Listing 和评论证据推断，不凭空画像。
4. 增长曲线：价格、BSR、评论或需求变化；没有历史数据就标为缺口。
5. 关键词与流量：核心词、长尾词、流量结构、竞争和意图。
6. 评论洞察：高频满意点、痛点、期望和样本限制。
7. 类目与竞争结构：品牌、商品、卖家类型集中度和头部壁垒。
8. 价格与痛点：价格带、价值锚点、可差异化问题和成本风险。
9. Listing 质量：标题、图片、卖点和信息表达；看不到素材时不得假装已审查。
10. 决策：做/不做/谨慎做、进入条件、验证计划和停止条件。

## 执行纪律

- 先规划最关键的数据缺口，再按需加载 `sellersprite`；需要历史、评论或跨平台补充时再加载 `sorftime`。
- 每个关键数字标注来源、日期、站点和口径。工具失败或空结果不算证据。
- 完整是决策链完整，不是工具数量多。无关模块可省略，但必须在覆盖说明中交代。

## 输出结构

先给结论摘要和数据快照，再给覆盖/限制、模块分析、机会、风险与反证，最后给行动建议和下一轮最低成本验证。不得编造任何数据、来源、趋势或已完成动作。""",
    },
)
