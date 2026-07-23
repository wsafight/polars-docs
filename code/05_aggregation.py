"""
05 · 配套代码：聚合、分组与窗口函数
=====================================================================
配合 src/content/docs/05-aggregation.md 阅读。

演示分组后的两种形状：
    1) 基础 agg（多聚合表达式，三方对照）
    2) 高级 agg（组内过滤/去重/按时间取值/分位数）
    3) over 窗口（城市总额 / 组内排名 / 组内占比）
    4) over 等价于 agg + join，但更优
    5) 多键分组

运行：
    uv run code/05_aggregation.py
"""

from __future__ import annotations

import duckdb
import polars as pl

from _common import (
    CUSTOMERS_PARQUET,
    ORDERS_PARQUET,
    PRODUCTS_PARQUET,
    ensure_data_exists,
    section,
    show,
)

# 复用的销售额表达式。
REVENUE = (
    pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
).alias("revenue")


def load_enriched() -> pl.DataFrame:
    """加载订单并 join 客户、商品维度，附加 revenue 列。

    返回:
        含 city / channel / revenue 等列的宽表，供全节复用。
    """
    return (
        pl.read_parquet(ORDERS_PARQUET)
        .join(pl.read_parquet(CUSTOMERS_PARQUET), on="customer_id")
        .join(pl.read_parquet(PRODUCTS_PARQUET), on="product_id")
        .with_columns(REVENUE)
    )


def demo_basic_agg() -> None:
    """演示基础分组聚合，并与 pandas、DuckDB 三方对照。

    任务：每个城市的订单数与总销售额。
    """
    section("1) 基础 agg（三方对照）")

    df = load_enriched()

    # —— Polars ——
    pl_res = (
        df.group_by("city")
        .agg(pl.len().alias("n"), pl.col("revenue").sum().round(2).alias("total"))
        .sort("total", descending=True)
    )
    show("Polars", pl_res)

    # —— pandas 命名聚合 ——
    pdf = df.select("city", "revenue").to_pandas()
    pd_res = (
        pdf.groupby("city", dropna=False)
        .agg(n=("revenue", "size"), total=("revenue", "sum"))
        .reset_index()
        .sort_values("total", ascending=False)
    )
    show("pandas", pd_res)

    # —— DuckDB ——
    sql = """
        SELECT city, COUNT(*) AS n, ROUND(SUM(revenue), 2) AS total
        FROM df_view GROUP BY city ORDER BY total DESC
    """
    duckdb.register("df_view", df.to_arrow())
    show("DuckDB", duckdb.sql(sql).pl())


def demo_advanced_agg() -> None:
    """演示 agg 中的高级表达式：组内过滤/去重/按时间取值/分位数。

    这些能力体现 Polars"agg 里能放任意表达式"，多数在标准 SQL 聚合中难以直接表达。
    """
    section("2) 高级 agg（组内过滤/去重/按时间取值/分位数）")

    df = load_enriched()
    result = (
        df.group_by("city")
        .agg(
            pl.len().alias("n"),
            # 组内只对 web 渠道求销售额。
            pl.col("revenue").filter(pl.col("channel") == "web").sum().round(2).alias("web_rev"),
            # 组内不同商品数（去重计数）。
            pl.col("product_id").n_unique().alias("n_products"),
            # 组内按下单时间排序后取最后一单的销售额。
            pl.col("revenue").sort_by("order_ts").last().round(2).alias("latest_rev"),
            # 组内销售额中位数。
            pl.col("revenue").quantile(0.5).round(2).alias("median_rev"),
        )
        .sort("n", descending=True)
    )
    show("高级聚合结果", result)


def demo_over() -> None:
    """演示 over 窗口函数：保持明细行数，附加分组统计。

    计算每笔订单所在城市的总销售额、组内排名、占比，
    结果行数与原始明细一致（不坍缩）。
    """
    section("3) over 窗口（城市总额/组内排名/组内占比）")

    df = load_enriched()
    result = df.select(
        "order_id",
        "city",
        "revenue",
        pl.col("revenue").sum().over("city").round(2).alias("city_total"),
        pl.col("revenue").rank("ordinal", descending=True).over("city").alias("rank_in_city"),
        (pl.col("revenue") / pl.col("revenue").sum().over("city")).round(4).alias("share"),
    ).sort("city", "rank_in_city")
    show("窗口结果（行数不变，附加组统计）", result.head(8))
    show("行数与原表一致", result.height)


def demo_over_vs_join() -> None:
    """证明 over 一步等价于"group_by 汇总再 join 回原表"，但更简洁高效。

    重要的坑：join 默认不匹配 null 键（nulls_equal=False），而本数据 city
    含 null，直接 join 会丢掉这些行、导致与 over 结果不一致。这恰恰说明
    over 更省心——它天然把 null 当作一个正常分组。要让 join 等价，需显式
    传 nulls_equal=True。
    """
    section("4) over 等价于 agg + join（含 null 键的坑）")

    df = load_enriched().select("order_id", "city", "revenue")

    # 写法 A：over 一步到位（null 自动作为一个分组）。
    via_over = df.with_columns(
        pl.col("revenue").sum().over("city").round(2).alias("city_total")
    )

    # 写法 B：先 agg 汇总，再 join 回原表。
    summary = df.group_by("city").agg(pl.col("revenue").sum().round(2).alias("city_total"))

    # 反例：默认 join 丢弃 null 键，行数变少、与 over 不一致。
    naive_join = df.join(summary, on="city")
    show("默认 join 行数（丢了 null 城市的行）", naive_join.height)

    # 正解：nulls_equal=True 让 null 也参与匹配，才与 over 等价。
    via_join = df.join(summary, on="city", nulls_equal=True)
    show("nulls_equal=True 的 join 行数", via_join.height)

    a = via_over.sort("order_id")
    b = via_join.sort("order_id").select(a.columns)
    show("over 与（正确的）agg+join 结果是否一致", a.equals(b))
    show("over 写法预览", a.head(5))


def demo_multi_key() -> None:
    """演示多键分组：按 (city, channel) 组合聚合。"""
    section("5) 多键分组 group_by(['city','channel'])")

    df = load_enriched()
    result = (
        df.group_by(["city", "channel"])
        .agg(pl.col("revenue").sum().round(2).alias("total"))
        .sort(["city", "total"], descending=[False, True])
    )
    show("多键分组结果", result.head(10))


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_basic_agg()
    demo_advanced_agg()
    demo_over()
    demo_over_vs_join()
    demo_multi_key()


if __name__ == "__main__":
    main()
