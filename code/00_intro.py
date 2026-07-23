"""
00 · 配套代码：四种写法端到端对照
=====================================================================
配合文档站首页（src/content/docs/index.md）阅读。

目标：用同一个任务——"按城市统计每笔订单的销售额之和，取 Top 3 城市"——
展示四种写法，让你在 30 秒内直观感受它们的差异：
    1) pandas          —— 命令式基准
    2) Polars Eager    —— 像 pandas 的即时执行
    3) Polars Lazy     —— 像 SQL 的惰性执行（优化器介入）
    4) DuckDB / SQL    —— 声明式基准

运行：
    uv run code/00_intro.py
"""

from __future__ import annotations

import duckdb
import pandas as pd
import polars as pl

from _common import (
    CUSTOMERS_PARQUET,
    ORDERS_PARQUET,
    PRODUCTS_PARQUET,
    ensure_data_exists,
    section,
    show,
)


def with_pandas() -> pd.DataFrame:
    """用 pandas（命令式）完成任务。

    典型 pandas 风格：逐步 merge、逐步赋值新列、groupby 后排序。
    每一步都立即执行，引擎没有全局视野。

    返回:
        Top 3 城市及其销售额的 pandas DataFrame。
    """
    orders = pd.read_parquet(ORDERS_PARQUET)
    customers = pd.read_parquet(CUSTOMERS_PARQUET)
    products = pd.read_parquet(PRODUCTS_PARQUET)

    # 两次 merge 把维度表拼到订单事实表上。
    df = orders.merge(customers, on="customer_id").merge(products, on="product_id")
    # 销售额 = 单价 × 数量 ×（1 - 折扣），折扣缺失按 0 处理。
    df["revenue"] = df["unit_price"] * df["quantity"] * (1 - df["discount"].fillna(0))
    # 按城市聚合求和，降序取前 3。
    result = (
        df.groupby("city")["revenue"]
        .sum()
        .sort_values(ascending=False)
        .head(3)
        .reset_index()
    )
    return result


def with_polars_eager() -> pl.DataFrame:
    """用 Polars Eager（急切模式）完成任务。

    风格接近 pandas：每一步立即执行、立即返回 DataFrame，
    但用的是 Polars 的表达式 API（pl.col(...)）。

    返回:
        Top 3 城市及其销售额的 Polars DataFrame。
    """
    orders = pl.read_parquet(ORDERS_PARQUET)
    customers = pl.read_parquet(CUSTOMERS_PARQUET)
    products = pl.read_parquet(PRODUCTS_PARQUET)

    return (
        orders.join(customers, on="customer_id")
        .join(products, on="product_id")
        # with_columns 新增 revenue 列；fill_null(0) 处理折扣缺失。
        .with_columns(
            (pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))).alias("revenue")
        )
        .group_by("city")
        .agg(pl.col("revenue").sum())
        .sort("revenue", descending=True)
        .head(3)
    )


def with_polars_lazy() -> pl.DataFrame:
    """用 Polars Lazy（惰性模式）完成任务。

    注意入口是 scan_parquet 而非 read_parquet：此时什么都还没执行，
    只是在搭建"查询计划"。直到 .collect() 才由优化器整体优化后执行。
    代码长得几乎和 Eager 一样，但执行语义完全不同（详见第 04 节）。

    返回:
        Top 3 城市及其销售额的 Polars DataFrame（collect 后的结果）。
    """
    orders = pl.scan_parquet(ORDERS_PARQUET)
    customers = pl.scan_parquet(CUSTOMERS_PARQUET)
    products = pl.scan_parquet(PRODUCTS_PARQUET)

    plan = (
        orders.join(customers, on="customer_id")
        .join(products, on="product_id")
        .with_columns(
            (pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))).alias("revenue")
        )
        .group_by("city")
        .agg(pl.col("revenue").sum())
        .sort("revenue", descending=True)
        .head(3)
    )
    # 打印优化后的执行计划，直观看到"优化器介入"这件事。
    print("\n--- Lazy 优化后的查询计划 ---")
    print(plan.explain())
    return plan.collect()


def with_duckdb() -> pl.DataFrame:
    """用 DuckDB / SQL（声明式）完成任务。

    你只描述"想要什么结果"，执行顺序完全交给 DuckDB 的优化器。
    DuckDB 可直接查询 Parquet 文件，无需先加载进内存。

    返回:
        Top 3 城市及其销售额（转换为 Polars DataFrame 便于统一展示）。
    """
    sql = f"""
        SELECT c.city,
               SUM(p.unit_price * o.quantity * (1 - COALESCE(o.discount, 0))) AS revenue
        FROM read_parquet('{ORDERS_PARQUET}') o
        JOIN read_parquet('{CUSTOMERS_PARQUET}') c ON o.customer_id = c.customer_id
        JOIN read_parquet('{PRODUCTS_PARQUET}') p ON o.product_id = p.product_id
        GROUP BY c.city
        ORDER BY revenue DESC
        LIMIT 3
    """
    # duckdb 的 .pl() 直接把结果转为 Polars DataFrame。
    return duckdb.sql(sql).pl()


def main() -> None:
    """依次运行四种写法并对照输出。

    你会看到：四种写法的"结果"完全一致，但"代码风格"沿着
    命令式 → 混合式 → 声明式 的光谱分布。这正是第 00 节的核心论点。
    """
    ensure_data_exists()

    section("任务：按城市统计销售额，取 Top 3")

    show("① pandas（命令式）", with_pandas())
    show("② Polars Eager（即时执行，像 pandas）", with_polars_eager())
    show("③ Polars Lazy（惰性执行，像 SQL）", with_polars_lazy())
    show("④ DuckDB / SQL（声明式）", with_duckdb())

    print(
        "\n观察：四者结果一致；风格从命令式(pandas) → 混合(Polars) → 声明式(SQL)。"
        "\nPolars Eager 手感像 pandas，Lazy 计划却像 SQL —— 这就是'手感+大脑'。"
    )


if __name__ == "__main__":
    main()
