"""
02 · 配套代码：表达式系统 Expression
=====================================================================
配合 src/content/docs/02-expressions.md 阅读。

演示 Polars 的灵魂——表达式：
    1) 表达式是"对象/配方"，不是结果
    2) 表达式可复用（一次定义，多处使用）
    3) 广播规则（col - col.mean() 中心化）
    4) when/then/otherwise 条件逻辑（对照 pandas np.select / DuckDB CASE WHEN）
    5) 表达式组合成复杂配方
    6) selectors 按规则批量选列
    7) map_elements（翻译腔）vs 原生表达式（地道）

运行：
    uv run code/02_expressions.py
"""

from __future__ import annotations

import warnings

import duckdb
import numpy as np
import polars as pl
import polars.selectors as cs

from _common import ORDERS_PARQUET, PRODUCTS_PARQUET, ensure_data_exists, section, show


def load_orders_with_price() -> pl.DataFrame:
    """加载订单并 join 商品单价，作为后续演示的公共数据。

    返回:
        含 unit_price / quantity / discount 的订单 DataFrame。
    """
    orders = pl.read_parquet(ORDERS_PARQUET)
    products = pl.read_parquet(PRODUCTS_PARQUET)
    return orders.join(products, on="product_id")


def demo_expr_is_object() -> None:
    """演示表达式只是"配方"对象，未绑定数据、未执行。

    直接打印一个 Expr，会看到它的结构描述而非任何数值结果——
    这印证"pandas 操作数据，Polars 操作对数据的描述"。
    """
    section("1) 表达式是对象/配方，不是结果")

    expr = (pl.col("unit_price") * pl.col("quantity")).alias("gross")
    # 打印的是表达式的结构，不是数字。
    show("一个未求值的 Expr 长这样", expr)
    show("它的类型", type(expr))


def demo_reuse() -> None:
    """演示同一个表达式在多个地方复用。

    定义一次 revenue 配方，分别用于：新增列、过滤、再聚合，
    避免 pandas 里"反复写同一段计算"的重复。
    """
    section("2) 表达式可复用")

    df = load_orders_with_price()
    # 定义一次"销售额"配方。
    revenue = (
        pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
    ).alias("revenue")

    # 复用 1：新增列。
    with_rev = df.with_columns(revenue)
    show("复用①：新增 revenue 列", with_rev.select("order_id", "revenue").head(3))

    # 复用 2：直接在 filter 里用（注意需重新引用列名或表达式）。
    big = with_rev.filter(pl.col("revenue") > 200)
    show("复用②：过滤高价订单数量", big.height)


def demo_broadcast() -> None:
    """演示广播规则：标量表达式自动广播到每一行。

    col - col.mean() 中，右侧是标量（全列均值），
    Polars 自动把它广播成与左侧等长，实现"中心化"。
    """
    section("3) 广播：col - col.mean() 中心化")

    df = load_orders_with_price().with_columns(
        (pl.col("unit_price") * pl.col("quantity")).alias("gross")
    )
    centered = df.select(
        "gross",
        # 每个 gross 减去全列均值：右侧标量被广播。
        (pl.col("gross") - pl.col("gross").mean()).alias("gross_centered"),
    )
    show("中心化结果（右侧均值被广播到每行）", centered.head(5))


def demo_when_then() -> None:
    """演示 when/then/otherwise 条件逻辑，并与 pandas、DuckDB 三方对照。

    任务：按折扣把订单分成 high / normal / none 三档。
    三种工具语义等价，但 Polars 用的是可组合的表达式。
    """
    section("4) when/then/otherwise（三方对照）")

    df = load_orders_with_price()

    # —— Polars：纯表达式，可链式、可嵌套 ——
    pl_res = df.select(
        "discount",
        pl.when(pl.col("discount").is_null())
        .then(pl.lit("none"))
        .when(pl.col("discount") > 0.2)
        .then(pl.lit("high"))
        .otherwise(pl.lit("normal"))
        .alias("level"),
    )
    show("Polars when/then", pl_res.head(6))

    # —— pandas：借助 numpy.select，跳出了 DataFrame API ——
    pdf = df.select("discount").to_pandas()
    conditions = [pdf["discount"].isna(), pdf["discount"] > 0.2]
    choices = ["none", "high"]
    pdf["level"] = np.select(conditions, choices, default="normal")
    show("pandas np.select", pdf.head(6))

    # —— DuckDB：声明式 CASE WHEN ——
    sql = f"""
        SELECT discount,
               CASE WHEN discount IS NULL THEN 'none'
                    WHEN discount > 0.2   THEN 'high'
                    ELSE 'normal' END AS level
        FROM read_parquet('{ORDERS_PARQUET}')
        LIMIT 6
    """
    show("DuckDB CASE WHEN", duckdb.sql(sql).pl())


def demo_compose() -> None:
    """演示把多个表达式组合成一个复杂配方。

    在一个 select 里同时算出多种派生指标，展示表达式的"乐高拼装"能力。
    """
    section("5) 表达式组合成复杂配方")

    df = load_orders_with_price()
    result = df.select(
        "order_id",
        (pl.col("unit_price") * pl.col("quantity")).alias("gross"),
        (pl.col("discount").fill_null(0) * 100).round(0).alias("discount_pct"),
        # 组合：净额 = 毛额 ×（1-折扣），再与 100 取较大值（保底）。
        pl.max_horizontal(
            (pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))),
            pl.lit(100.0),
        ).alias("net_with_floor"),
    )
    show("组合出的多个派生指标", result.head(5))


def demo_selectors() -> None:
    """演示 selectors 按规则批量选列。

    用 cs.numeric() 一次性选中所有数值列并求和，
    无需手写每个列名，且随 schema 变化自动适配。
    """
    section("6) selectors 按规则批量选列")

    df = load_orders_with_price().with_columns(
        (pl.col("unit_price") * pl.col("quantity")).alias("gross")
    )
    # 对所有数值列求和（selectors 自动匹配）。
    show("所有数值列求和", df.select(cs.numeric().sum()))
    show("所有字符串列转大写（前3行）", df.select(cs.string().str.to_uppercase()).head(3))


def demo_apply_vs_native() -> None:
    """对比 map_elements（翻译腔）与原生表达式（地道写法）。

    两者结果相同，但 map_elements 逐行回调 Python 函数、丢失向量化，
    应尽量避免。第 11 节会用 benchmark 量化其性能差距。
    """
    section("7) map_elements（翻译腔）vs 原生表达式（地道）")

    df = pl.DataFrame({"x": [1, 2, 3, 4, 5]})

    # 翻译腔：逐元素回调 Python 函数（慢，仅作反例）。
    # 这里 map_elements 会触发 PolarsInefficientMapWarning——这正是我们想演示的
    # "低效信号"，属预期行为，故局部抑制，避免污染教学输出。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pl.exceptions.PolarsInefficientMapWarning)
        slow = df.select(
            pl.col("x").map_elements(lambda v: v * v + 1, return_dtype=pl.Int64).alias("y")
        )
    show("map_elements（反例）", slow)

    # 地道：纯表达式，向量化执行。
    fast = df.select((pl.col("x") ** 2 + 1).alias("y"))
    show("原生表达式（推荐）", fast)


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_expr_is_object()
    demo_reuse()
    demo_broadcast()
    demo_when_then()
    demo_compose()
    demo_selectors()
    demo_apply_vs_native()


if __name__ == "__main__":
    main()
