"""
07 · 配套代码：数据重塑 Reshape
=====================================================================
配合 src/content/docs/07-reshape.md 阅读。

演示长表与宽表的互转，以及 list 列的炸开：
    1) pivot：长表 → 宽表（销售额矩阵，对照 pandas pivot_table）
    2) pivot → unpivot：恢复聚合后的长表（不恢复原始明细）
    3) unpivot 实战：宽表外部数据 → 长表再聚合
    4) explode：把商品列表炸开成逐条明细
    5) group_by agg（收拢成 list）↔ explode（炸开）互逆
    6) partition_by：按键物理拆成多个 DataFrame

运行：
    uv run code/07_reshape.py
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

REVENUE = (
    pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
).alias("revenue")


def load_enriched() -> pl.DataFrame:
    """加载订单并 join 客户、商品，附加 revenue。

    返回:
        含 city / channel / revenue 的宽表。
    """
    return (
        pl.read_parquet(ORDERS_PARQUET)
        .join(pl.read_parquet(CUSTOMERS_PARQUET), on="customer_id")
        .join(pl.read_parquet(PRODUCTS_PARQUET), on="product_id")
        .with_columns(REVENUE)
    )


def demo_pivot() -> None:
    """演示 pivot：把长表按 (city × channel) 摊成销售额矩阵。

    channel 的每个唯一值（web/app/store）升格为一列；
    因每个 (city, channel) 对应多笔订单，用 sum 聚合。
    """
    section("1) pivot：长表 → 宽表（销售额矩阵）")

    df = load_enriched()
    wide = df.pivot(
        on="channel",
        index="city",
        values="revenue",
        aggregate_function="sum",
    )
    show("pivot 后的 city × channel 矩阵", wide)


def demo_pivot_unpivot_roundtrip() -> None:
    """演示 pivot 后再 unpivot 会恢复聚合粒度的长表。

    由于 pivot 使用 sum 合并了同一 (city, channel) 的多笔订单，原始明细
    已经丢失；unpivot 只能恢复每个城市/渠道一行的聚合长表。
    """
    section("2) pivot → unpivot（恢复聚合后的长表）")

    df = load_enriched()
    wide = df.pivot(on="channel", index="city", values="revenue", aggregate_function="sum")

    # 把 web/app/store 列折叠回 (channel, revenue) 两列。
    long_again = wide.unpivot(
        index="city",
        variable_name="channel",
        value_name="revenue",
    )
    show("unpivot 折回的长表", long_again.sort("city", "channel"))
    show("原始明细行数", df.height)
    show("聚合长表行数（信息已压缩）", long_again.height)


def demo_unpivot_external() -> None:
    """unpivot 实战：模拟外部宽表数据，折成长表后聚合。

    外部（如 Excel）常给出"每季度一列"的宽表；unpivot 成长表后，
    聚合与绘图都更方便——这是常见的清洗第一步。
    """
    section("3) unpivot 实战：宽表外部数据 → 长表")

    # 模拟一个"每季度一列"的宽表。
    external = pl.DataFrame(
        {
            "product": ["A", "B", "C"],
            "Q1": [100, 200, 300],
            "Q2": [110, 190, 320],
            "Q3": [120, 210, 310],
        }
    )
    show("外部宽表", external)

    long = external.unpivot(
        index="product",
        on=["Q1", "Q2", "Q3"],
        variable_name="quarter",
        value_name="sales",
    )
    show("unpivot 成长表", long)

    # 折成长表后，按季度聚合易如反掌。
    show("按季度汇总", long.group_by("quarter").agg(pl.col("sales").sum()).sort("quarter"))


def demo_explode() -> None:
    """演示 explode：把每个客户的商品列表炸开成逐条明细。

    先按客户把 product_id 收拢成 list，再 explode 展开，
    直观呈现"一格多值 → 多行单值"。
    """
    section("4) explode：把商品列表炸开成明细")

    df = load_enriched()
    # 先把每个客户购买的商品收拢成一个 list（取前 3 个客户演示）。
    grouped = (
        df.group_by("customer_id")
        .agg(pl.col("product_id").alias("products"))
        .sort("customer_id")
        .head(3)
    )
    show("收拢：每个客户的商品 list", grouped)

    # explode 炸开：list 里每个元素成为独立一行。
    # empty_as_null=True 显式保持当前行为（未来的 Polars 2.0 起该默认值将改为 False）。
    exploded = grouped.explode("products", empty_as_null=True)
    show("炸开后的逐条明细", exploded)


def demo_agg_explode_inverse() -> None:
    """演示 group_by-agg（收拢成 list）与 explode（炸开）互为逆操作。

    对同一份数据先收拢再炸开，行数与内容应回到原状（顺序内聚合）。
    """
    section("5) agg 收拢 ↔ explode 炸开 互逆")

    df = pl.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3]})
    collapsed = df.group_by("g").agg(pl.col("v")).sort("g")
    show("收拢成 list", collapsed)

    restored = collapsed.explode("v", empty_as_null=True).sort(["g", "v"])
    show("explode 还原", restored)
    show("与原表内容一致", restored.equals(df.sort(["g", "v"])))


def demo_partition_by() -> None:
    """演示 partition_by：按渠道把表物理拆成多个独立 DataFrame。

    区别于 group_by（聚合成一张表），partition_by 返回多个 DataFrame，
    适合"分组后分别处理/分别导出"。
    """
    section("6) partition_by：按键物理拆表")

    df = load_enriched().select("order_id", "channel", "revenue")
    parts = df.partition_by("channel", as_dict=True)
    for key, sub in parts.items():
        # key 是元组（如 ('web',)），取第一个元素作为渠道名。
        show(f"渠道 {key[0]} 的子表行数", sub.height)


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_pivot()
    demo_pivot_unpivot_roundtrip()
    demo_unpivot_external()
    demo_explode()
    demo_agg_explode_inverse()
    demo_partition_by()


if __name__ == "__main__":
    main()
