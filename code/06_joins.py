"""
06 · 配套代码：连接 Join 与拼接
=====================================================================
配合 src/content/docs/06-joins.md 阅读。

演示：
    1) 五种 how（inner/left/full/semi/anti）
    2) anti join 实战：找出从未下单的客户
    3) null 键的坑（nulls_equal 开关）
    4) validate 基数校验
    5) join_asof 时间序列最近匹配
    6) concat 纵向 / 对角拼接

运行：
    uv run code/06_joins.py
"""

from __future__ import annotations

import polars as pl

from _common import (
    CUSTOMERS_PARQUET,
    ORDERS_PARQUET,
    PRODUCTS_PARQUET,
    ensure_data_exists,
    section,
    show,
)


def demo_how_strategies() -> None:
    """演示五种 join 策略的行为差异。

    用一个精简的左右表，清晰展示 inner/left/full/semi/anti 各保留哪些行。
    """
    section("1) 五种 how 策略")

    left = pl.DataFrame({"k": [1, 2, 3], "x": ["a", "b", "c"]})
    right = pl.DataFrame({"k": [2, 3, 4], "y": ["B", "C", "D"]})
    show("左表", left)
    show("右表", right)

    for how in ["inner", "left", "full", "semi", "anti"]:
        # semi/anti 只用右表做过滤，结果不含右表的列。
        show(f"how='{how}'", left.join(right, on="k", how=how))


def demo_anti_in_action() -> None:
    """anti join 实战：找出"从未有过大额订单"的客户。

    本数据 5020 笔订单几乎覆盖全部客户，"从未下单"差集为空，
    因此换一个更有业务意义的问题：谁从未产生过 revenue>200 的大额订单。
    左表全部客户 anti 右表"有过大额订单的客户 id"，直接得到差集，
    这在 pandas 里通常要用 isin + 取反迂回实现。
    """
    section("2) anti join：从未有过大额订单的客户")

    customers = pl.read_parquet(CUSTOMERS_PARQUET)
    orders = pl.read_parquet(ORDERS_PARQUET)
    products = pl.read_parquet(PRODUCTS_PARQUET)

    revenue = (
        pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
    ).alias("revenue")

    # 有过大额订单（revenue>200）的客户 id（去重）。
    big_spenders = (
        orders.join(products, on="product_id")
        .with_columns(revenue)
        .filter(pl.col("revenue") > 200)
        .select("customer_id")
        .unique()
    )
    # anti：保留 customers 中不在 big_spenders 里的行。
    never = customers.join(big_spenders, on="customer_id", how="anti")
    show("有过大额订单的客户数", big_spenders.height)
    show("从未有过大额订单的客户数", never.height)
    show("预览", never.select("customer_id", "name", "tier").head(5))


def demo_null_key_pitfall() -> None:
    """演示 null 键默认不匹配的坑，以及 nulls_equal 开关。

    键列含 null 时，默认 join 会丢弃这些行（SQL 的 NULL != NULL 语义），
    需显式 nulls_equal=True 才让 null 参与匹配。
    """
    section("3) null 键的坑（nulls_equal）")

    left = pl.DataFrame({"k": ["a", None, "b"], "x": [1, 2, 3]})
    right = pl.DataFrame({"k": ["a", None, "b"], "y": [10, 20, 30]})

    default_join = left.join(right, on="k")
    show("默认 join（null 行被丢，只剩 2 行）", default_join)

    nulls_join = left.join(right, on="k", nulls_equal=True)
    show("nulls_equal=True（null 也匹配，3 行）", nulls_join)


def demo_validate() -> None:
    """演示 validate 基数校验如何在假设不成立时报错保护。

    对"一对一"假设的错误数据启用 validate='1:1' 会抛异常，
    及早暴露数据质量问题，而非默默产出膨胀结果。
    """
    section("4) validate 基数校验")

    left = pl.DataFrame({"k": [1, 2], "x": ["a", "b"]})
    # 右表 k=1 重复，违反 1:1 假设。
    right_dup = pl.DataFrame({"k": [1, 1, 2], "y": [10, 11, 20]})

    # 不校验时：行数被放大（1 对 2）。
    show("不校验 → 行数被放大", left.join(right_dup, on="k").height)

    # 校验 1:1 时：Polars 主动报错。
    try:
        left.join(right_dup, on="k", validate="1:1")
    except Exception as exc:  # noqa: BLE001  # 教学用途：展示异常信息
        show("validate='1:1' 主动报错", f"{type(exc).__name__}: {exc}")


def demo_join_asof() -> None:
    """演示 join_asof 的时间序列"最近匹配"。

    给每笔交易匹配"下单时刻最新的报价"，backward 取 ≤ 当前时间的最近一条。
    前提：两表都按 as-of 键有序。
    """
    section("5) join_asof 最近匹配")

    # 报价表：某几个时间点的报价。
    quotes = pl.DataFrame({"t": [1, 5, 10], "quote": [100, 105, 110]}).sort("t")
    # 交易表：发生在任意时刻的交易。
    trades = pl.DataFrame({"t": [2, 6, 7, 12], "trade_id": ["x", "y", "z", "w"]}).sort("t")

    backward = trades.join_asof(quotes, on="t", strategy="backward")
    show("backward：匹配 ≤ 交易时刻的最近报价", backward)

    forward = trades.join_asof(quotes, on="t", strategy="forward")
    show("forward：匹配 ≥ 交易时刻的最近报价", forward)


def demo_concat() -> None:
    """演示 concat 的纵向与对角拼接。

    vertical 要求列一致；diagonal 允许列集合不同，取并集、缺失补 null，
    适合合并 schema 略有差异的多批数据。
    """
    section("6) concat 纵向 / 对角拼接")

    jan = pl.DataFrame({"id": [1, 2], "amt": [10, 20]})
    feb = pl.DataFrame({"id": [3, 4], "amt": [30, 40]})
    show("vertical 纵向堆叠", pl.concat([jan, feb], how="vertical"))

    # feb2 多了一列 note，用 diagonal 合并，jan 缺失处补 null。
    feb2 = pl.DataFrame({"id": [3, 4], "amt": [30, 40], "note": ["x", "y"]})
    show("diagonal 对角拼接（缺列补 null）", pl.concat([jan, feb2], how="diagonal"))


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_how_strategies()
    demo_anti_in_action()
    demo_null_key_pitfall()
    demo_validate()
    demo_join_asof()
    demo_concat()


if __name__ == "__main__":
    main()
