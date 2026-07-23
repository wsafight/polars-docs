"""
14 · 配套代码：端到端实战闭环
=====================================================================
配合 src/content/docs/14-end-to-end.md 阅读。

一个完整、函数化、纯 Lazy 的电商 ETL 管道：
    build_clean_orders()             扫描 + 清洗 + join → 干净宽表
    analysis_monthly_channel()       月度各渠道销售额趋势
    analysis_city_top_category()     每城市 Top 类目（over 窗口排名）
    analysis_high_value_customers()  高价值客户识别
    + explain 展示优化计划
    + sink_parquet 流式落盘

设计要点：每段逻辑封装成返回 LazyFrame 的函数，清洗与分析分离；
三个分析用 collect_all 一次提交，让公共上游计划只执行一次。

运行：
    uv run code/14_end_to_end.py
"""

from __future__ import annotations

import polars as pl

from _common import (
    CUSTOMERS_PARQUET,
    DATA_DIR,
    ORDERS_PARQUET,
    PRODUCTS_PARQUET,
    ensure_data_exists,
    section,
    show,
)

# 销售额表达式：单价 × 数量 ×（1 - 折扣）。清洗阶段已把折扣缺失填 0。
REVENUE = (
    pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount"))
).alias("revenue")


def build_clean_orders() -> pl.LazyFrame:
    """扫描三张原始表，清洗并 join 成一个干净的订单宽表（LazyFrame）。

    这是整条管道的"提取 + 清洗 + 关联"阶段，产出供各分析复用的干净宽表。
    全程惰性：返回的是计划，尚未执行。清洗步骤复用第 13 节的流水线顺序。

    返回:
        含 city / channel / category / revenue 等列的干净宽表 LazyFrame。
    """
    orders = (
        pl.scan_parquet(ORDERS_PARQUET)
        .unique()                                    # 去重（第13节）
        .with_columns(
            pl.col("note").str.strip_chars().str.to_lowercase().alias("note"),  # 洗字符串
            pl.col("discount").fill_null(0.0),       # 缺失填 0（第13节）
        )
        .filter(pl.col("discount").is_between(0, 1))  # 过滤异常折扣
    )
    customers = pl.scan_parquet(CUSTOMERS_PARQUET)
    products = pl.scan_parquet(PRODUCTS_PARQUET)

    # 关联维表并派生销售额。
    return (
        orders.join(customers, on="customer_id", validate="m:1")  # 维表键必须唯一
        .join(products, on="product_id", validate="m:1")
        .with_columns(REVENUE)                       # 派生列（第02/03节）
    )


def analysis_monthly_channel(clean: pl.LazyFrame) -> pl.LazyFrame:
    """分析①：每月各渠道的销售额趋势。

    用 group_by_dynamic 按月分桶（第09节）+ 分组键 channel（第05节）。

    参数:
        clean: 干净宽表 LazyFrame。
    返回:
        (channel, 月份, 销售额) 的惰性查询计划。
    """
    return (
        clean.sort("order_ts")
        .group_by_dynamic("order_ts", every="1mo", group_by="channel")
        .agg(pl.col("revenue").sum().round(2).alias("monthly_rev"))
        .sort("channel", "order_ts")
    )


def analysis_city_top_category(clean: pl.LazyFrame) -> pl.LazyFrame:
    """分析②：每个城市销售额最高的商品类目。

    先按 (city, category) 聚合，再用 over 窗口在每个城市内排名（第05节），
    取排名第 1 的类目；如果销售额并列，则保留所有并列第一。

    参数:
        clean: 干净宽表 LazyFrame。
    返回:
        每个城市 Top 类目的惰性查询计划。
    """
    return (
        clean.group_by("city", "category")
        .agg(pl.col("revenue").sum().round(2).alias("cat_rev"))
        # dense 排名会把并列最高值都标为 1，避免任意丢掉业务上的并列第一。
        .with_columns(
            pl.col("cat_rev").rank("dense", descending=True).over("city").alias("rank_in_city")
        )
        .filter(pl.col("rank_in_city") == 1)
        .sort(["cat_rev", "city", "category"], descending=[True, False, False], nulls_last=True)
    )


def analysis_high_value_customers(
    clean: pl.LazyFrame, threshold: float = 2500.0
) -> pl.LazyFrame:
    """分析③：识别累计消费超过阈值的高价值客户。

    按客户聚合总销售额（第05节），再用阈值 filter（第03节）筛选。

    参数:
        clean:     干净宽表 LazyFrame。
        threshold: 高价值客户的累计消费阈值。
    返回:
        高价值客户及其累计消费、订单数的惰性查询计划。
    """
    return (
        clean.group_by("customer_id")
        .agg(
            pl.col("revenue").sum().round(2).alias("total_spent"),
            pl.len().alias("n_orders"),
        )
        .filter(pl.col("total_spent") > threshold)
        .sort("total_spent", descending=True)
    )


def demo_explain(clean: pl.LazyFrame) -> None:
    """打印分析①主管道的优化计划，展示能力组合后优化器如何安排。"""
    section("优化计划：能力组合后优化器怎么安排")

    plan = (
        clean.sort("order_ts")
        .group_by_dynamic("order_ts", every="1mo", group_by="channel")
        .agg(pl.col("revenue").sum())
    )
    # 计划里应能看到多表 join、投影下推（只读用到的列）等优化。
    print(plan.explain())


def demo_sink(clean: pl.LazyFrame) -> None:
    """把清洗后的宽表用 sink_parquet 流式落盘，演示完整 Load 阶段。"""
    section("落盘：sink_parquet 流式写出干净宽表")

    out = DATA_DIR / "_clean_orders.parquet"
    # select 只保留分析需要的列后流式写出（不 collect 整表）。
    try:
        clean.select(
            "order_id", "order_ts", "city", "channel", "category", "revenue"
        ).sink_parquet(out)
        show("落盘行数", pl.scan_parquet(out).select(pl.len()).collect().item())
    finally:
        out.unlink(missing_ok=True)


def main() -> None:
    """运行完整 ETL：构建干净宽表 → 三个分析 → explain → 落盘。"""
    ensure_data_exists()

    # 提取 + 清洗 + 关联：得到供所有分析复用的干净宽表（仍是计划）。
    clean = build_clean_orders()

    monthly_plan = analysis_monthly_channel(clean)
    city_top_plan = analysis_city_top_category(clean)
    high_value_plan = analysis_high_value_customers(clean)

    # 一次提交三个分叉查询。collect_all 会组合计划并做公共子计划消除，
    # 因而共同的 scan / unique / join 上游只执行一次。
    monthly, city_top, high_value = pl.collect_all(
        [monthly_plan, city_top_plan, high_value_plan]
    )

    section("分析①：每月各渠道销售额趋势")
    show("月度渠道趋势", monthly)

    section("分析②：每个城市的 Top 商品类目（并列保留）")
    show("城市 Top 类目", city_top)

    section("分析③：高价值客户（累计消费 > 2500）")
    show("高价值客户数", high_value.height)
    show("Top 10", high_value.head(10))

    demo_explain(clean)
    demo_sink(clean)


if __name__ == "__main__":
    main()
