"""
12 · 配套代码：从 pandas 迁移与 SQL 接口
=====================================================================
配合 src/content/docs/12-migration-sql.md 阅读。

演示：
    1) 一段 pandas 代码 → 地道 Polars 的对照改写
    2) index 的消失（set_index/loc 的等价写法）
    3) NaN vs null 迁移坑（fillna 行为差异）
    4) pandas ↔ Polars 互转的复制与 dtype 语义
    5) pl.sql() 原生 SQL
    6) pl.SQLContext 多表 SQL join（与表达式写法对照）

运行：
    uv run code/12_migration_sql.py
"""

from __future__ import annotations

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


def demo_side_by_side() -> None:
    """同一任务的 pandas 与地道 Polars 对照改写。

    任务：过滤 quantity>=3 的订单，新增 gross 列，按 channel 求平均 gross。
    展示"赋值列→with_columns、布尔索引→filter、groupby→group_by.agg"的迁移。
    """
    section("1) pandas → 地道 Polars 对照")

    orders = pl.read_parquet(ORDERS_PARQUET).join(
        pl.read_parquet(PRODUCTS_PARQUET), on="product_id"
    )

    # —— pandas 风格 ——
    pdf = orders.to_pandas()
    pdf = pdf[pdf["quantity"] >= 3]              # 布尔索引过滤
    pdf["gross"] = pdf["unit_price"] * pdf["quantity"]  # 赋值新列
    pd_res = pdf.groupby("channel")["gross"].mean().reset_index()  # 分组聚合
    show("pandas 写法", pd_res)

    # —— 地道 Polars ——
    pl_res = (
        orders.filter(pl.col("quantity") >= 3)                    # filter 上下文
        .with_columns((pl.col("unit_price") * pl.col("quantity")).alias("gross"))  # with_columns
        .group_by("channel")                                      # group_by.agg
        .agg(pl.col("gross").mean().alias("gross"))
    )
    show("地道 Polars 写法", pl_res)


def demo_no_index() -> None:
    """演示 pandas 的 index 操作在 Polars 中的等价写法。

    pandas 常用 set_index + loc[label] 定位；Polars 无 index，
    改用 filter 按条件选行、用列选择取列。
    """
    section("2) index 的消失")

    df = pl.DataFrame({"name": ["a", "b", "c"], "score": [90, 80, 70]})

    # pandas: df.set_index('name').loc['b'] —— Polars 用 filter。
    show("pandas 的 loc['b'] 等价于 filter", df.filter(pl.col("name") == "b"))

    # pandas 常见的 reset_index() 在 Polars 里根本不需要（本就没有 index）。
    show("Polars 无需 reset_index，行本就按位置", df.with_row_index("idx"))


def demo_nan_null_migration() -> None:
    """演示 fillna 的迁移坑：pandas 用 NaN，Polars 区分 null 与 NaN。

    pandas 的 fillna 同时处理 NaN；Polars 需明确是填 null 还是 NaN。
    """
    section("3) NaN vs null 迁移坑")

    # pandas：缺失是 NaN，fillna 一把梭。
    pdf = pd.DataFrame({"v": [1.0, None, 3.0]})
    pdf["filled"] = pdf["v"].fillna(0)
    show("pandas fillna(0)", pdf)

    # Polars：缺失是 null，用 fill_null；若还有 NaN 要另用 fill_nan。
    plf = pl.DataFrame({"v": [1.0, None, 3.0]})
    show(
        "Polars fill_null(0)",
        plf.with_columns(pl.col("v").fill_null(0).alias("filled")),
    )


def demo_roundtrip() -> None:
    """演示 pandas ↔ Polars 互转，并明确默认复制与 Arrow dtype 语义。"""
    section("4) pandas ↔ Polars 往返")

    pdf = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    plf = pl.from_pandas(pdf)          # pandas → Polars
    back = plf.to_pandas()             # 默认转 NumPy-backed pandas，会复制
    arrow_back = plf.to_pandas(use_pyarrow_extension_array=True)
    show("from_pandas 后的 schema", plf.schema)
    show("默认往返后简单值一致", back.equals(pdf))
    show("Arrow-backed pandas dtypes（可减少复制并保留 null）", arrow_back.dtypes)


def demo_pl_sql() -> None:
    """演示 pl.sql()：直接对同名变量对应的表跑 SQL。"""
    section("5) pl.sql() 原生 SQL")

    # 变量名 orders 会被 pl.sql 自动注册为 SQL 表名（静态检查器看不到这层
    # 隐式引用，可能提示"未使用"，实际在下面的 SQL 字符串中被引用）。
    orders = pl.read_parquet(ORDERS_PARQUET)  # noqa: F841
    result = pl.sql(
        "SELECT channel, COUNT(*) AS n FROM orders GROUP BY channel ORDER BY n DESC",
        eager=True,
    )
    show("pl.sql 查询结果", result)


def demo_sql_context() -> None:
    """演示 pl.SQLContext 多表 join，并与表达式 API 结果对照。

    注册 orders / customers 两张表，用 SQL 做 join + 分组，
    再用等价的表达式 API 复现，验证两条路径结果一致。
    """
    section("6) pl.SQLContext 多表 join（与表达式对照）")

    orders = pl.read_parquet(ORDERS_PARQUET).lazy()
    customers = pl.read_parquet(CUSTOMERS_PARQUET).lazy()

    # —— SQL 路径 ——
    ctx = pl.SQLContext(o=orders, c=customers)
    sql_res = ctx.execute(
        """
        SELECT c.city, COUNT(*) AS n
        FROM o JOIN c ON o.customer_id = c.customer_id
        GROUP BY c.city
        ORDER BY n DESC
        """,
        eager=True,
    )
    show("SQLContext 结果", sql_res)

    # —— 表达式路径（应一致）——
    expr_res = (
        orders.join(customers, on="customer_id")
        .group_by("city")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
        .collect()
    )
    show("表达式 API 结果", expr_res)
    show("两条路径结果一致", sql_res.equals(expr_res))


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_side_by_side()
    demo_no_index()
    demo_nan_null_migration()
    demo_roundtrip()
    demo_pl_sql()
    demo_sql_context()


if __name__ == "__main__":
    main()
