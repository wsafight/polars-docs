"""
03 · 配套代码：四大上下文 Contexts
=====================================================================
配合 src/content/docs/03-contexts.md 阅读。

演示表达式的四个求值环境：
    1) select        —— 选择 + 派生 + 聚合广播
    2) with_columns  —— 保留全表 + 新增/替换列
    3) filter        —— 多条件筛行（对照 SQL WHERE）
    4) group_by().agg—— 分组聚合，含"组内先过滤再聚合"的高级表达式
    5) 四上下文串成完整管道，与等价 SQL 对照

运行：
    uv run code/03_contexts.py
"""

from __future__ import annotations

import duckdb
import polars as pl

from _common import ORDERS_PARQUET, PRODUCTS_PARQUET, ensure_data_exists, section, show

# 全局复用的"销售额"表达式配方（第 02 节思想：定义一次，多处复用）。
REVENUE = (
    pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
).alias("revenue")


def load() -> pl.DataFrame:
    """加载订单并 join 单价，作为演示数据。

    返回:
        含单价的订单 DataFrame。
    """
    return pl.read_parquet(ORDERS_PARQUET).join(
        pl.read_parquet(PRODUCTS_PARQUET), on="product_id"
    )


def demo_select() -> None:
    """演示 select 上下文：选择、派生、聚合广播三合一。

    结果只包含列出的表达式；聚合表达式（均值）被广播到与逐元素列等长。
    """
    section("1) select：选择 + 派生 + 聚合广播")

    df = load()
    result = df.select(
        pl.col("order_id"),                          # 原样保留
        REVENUE,                                     # 派生新列
        pl.col("discount").mean().alias("avg_disc"), # 聚合成标量，自动广播到每行
    )
    show("select 结果（avg_disc 每行都相同 = 广播）", result.head(5))


def demo_with_columns() -> None:
    """演示 with_columns 上下文：保留全表并新增列，同名则替换。

    对照 select：这里原有全部列都保留，只是多出 revenue 列；
    再演示用同名表达式"替换"列（Polars 没有 inplace，返回新对象）。
    """
    section("2) with_columns：保留全表 + 新增/替换列")

    df = load().select("order_id", "unit_price", "quantity", "discount")
    # 新增 revenue：原 4 列全部保留，结果变 5 列。
    added = df.with_columns(REVENUE)
    show("新增列后仍保留原列", added.head(3))

    # 同名替换：把 discount 缺失填 0，直接覆盖原列。
    replaced = df.with_columns(pl.col("discount").fill_null(0.0))
    show("同名替换 discount（null→0）", replaced.head(5))


def demo_filter() -> None:
    """演示 filter 上下文：多条件组合筛行。

    多条件用 & | ~，每个条件必须加括号（Python 运算符优先级）。
    对标 SQL 的 WHERE 子句。
    """
    section("3) filter：多条件筛行（对照 SQL WHERE）")

    df = load()
    # 折扣 > 0.1 且渠道为 web —— 注意每个条件都用括号包住。
    result = df.filter((pl.col("discount") > 0.1) & (pl.col("channel") == "web"))
    show("web 渠道且折扣>0.1 的行数", result.height)
    show("预览", result.select("order_id", "channel", "discount").head(3))


def demo_group_by_agg() -> None:
    """演示 group_by().agg 上下文，从简单聚合到高级组内表达式。

    agg 里可放任意复杂表达式：本例除常规 sum/mean 外，
    还演示"组内先按条件过滤再求和"这种 SQL 难以直接表达的能力。
    """
    section("4) group_by().agg：分组聚合与高级表达式")

    df = load().with_columns(REVENUE)
    result = (
        df.group_by("channel")
        .agg(
            pl.len().alias("n_orders"),                       # 每组行数
            pl.col("revenue").sum().round(2).alias("total"),  # 每组销售额
            pl.col("revenue").mean().round(2).alias("avg"),   # 每组均值
            # 高级：组内只对折扣>0.2 的订单求和（组内过滤再聚合）。
            pl.col("revenue").filter(pl.col("discount") > 0.2).sum().round(2).alias("high_disc_rev"),
        )
        .sort("total", descending=True)
    )
    show("按渠道聚合（含组内过滤聚合）", result)


def demo_pipeline_vs_sql() -> None:
    """演示四大上下文串成完整管道，并与等价 SQL 三方对照。

    Polars 管道的书写顺序 filter→with_columns→group_by→sort，
    几乎逐句对应 SQL 的 WHERE→SELECT→GROUP BY→ORDER BY。
    """
    section("5) 完整管道 vs 等价 SQL")

    df = load()
    pl_result = (
        df.filter(pl.col("discount").is_not_null())  # WHERE
        .with_columns(REVENUE)                        # 派生列
        .group_by("channel")                          # GROUP BY
        .agg(pl.col("revenue").sum().round(2).alias("total"))
        .sort("total", descending=True)               # ORDER BY
    )
    show("Polars 管道", pl_result)

    sql = f"""
        SELECT o.channel,
               ROUND(SUM(p.unit_price * o.quantity * (1 - o.discount)), 2) AS total
        FROM read_parquet('{ORDERS_PARQUET}') o
        JOIN read_parquet('{PRODUCTS_PARQUET}') p ON o.product_id = p.product_id
        WHERE o.discount IS NOT NULL
        GROUP BY o.channel
        ORDER BY total DESC
    """
    show("等价 DuckDB SQL", duckdb.sql(sql).pl())


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_select()
    demo_with_columns()
    demo_filter()
    demo_group_by_agg()
    demo_pipeline_vs_sql()


if __name__ == "__main__":
    main()
