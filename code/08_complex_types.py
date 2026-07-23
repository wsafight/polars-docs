"""
08 · 配套代码：字符串 · List · Struct 复杂类型
=====================================================================
配合 src/content/docs/08-complex-types.md 阅读。

演示三大命名空间：
    1) .str 清洗脏字符串（strip + lower，对照 pandas）
    2) .str 正则提取
    3) .list 就地计算（len/sum/eval，对照 explode 路线）
    4) .struct 打包/解包/unnest
    5) .struct 承载 value_counts 的多值结果
    6) 跨命名空间链式（str → list）

运行：
    uv run code/08_complex_types.py
"""

from __future__ import annotations

import polars as pl

from _common import ORDERS_PARQUET, ensure_data_exists, section, show


def demo_str_cleaning() -> None:
    """演示 .str 清洗脏字符串，并与 pandas 对照。

    数据里的 note 列含首尾空格、大小写混乱；用 strip_chars + to_lowercase
    归一化后统计分布，可见脏值被正确合并。
    """
    section("1) .str 清洗脏字符串（三方对照）")

    orders = pl.read_parquet(ORDERS_PARQUET)

    # Polars：向量化清洗。
    cleaned = orders.select(
        pl.col("note").alias("raw"),
        pl.col("note").str.strip_chars().str.to_lowercase().alias("clean"),
    )
    show("原始 vs 清洗后（前 6 行）", cleaned.head(6))

    # 清洗后按值统计分布：脏值（如 ' OK ' 与 'ok'）被合并。
    dist = (
        orders.select(pl.col("note").str.strip_chars().str.to_lowercase().alias("clean"))
        .group_by("clean")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
    )
    show("清洗后 note 分布", dist)

    # pandas 对照：语义类似但字符串操作走较慢路径。
    pdf = orders.select("note").to_pandas()
    pdf["clean"] = pdf["note"].str.strip().str.lower()
    show("pandas 清洗（前 6 行）", pdf.head(6))


def demo_str_regex() -> None:
    """演示 .str 正则提取。

    从形如 'prod_007' 的字符串中提取数字编号，展示 extract 捕获组。
    """
    section("2) .str 正则提取")

    df = pl.DataFrame({"code": ["prod_007", "prod_042", "prod_123"]})
    result = df.select(
        "code",
        # 提取第 1 个捕获组（括号内的数字）。
        pl.col("code").str.extract(r"prod_(\d+)", 1).alias("num_str"),
        # 提取后转成整数。
        pl.col("code").str.extract(r"prod_(\d+)", 1).cast(pl.Int64).alias("num_int"),
    )
    show("正则提取编号", result)


def demo_list_inplace() -> None:
    """演示 .list 就地计算，并对照 explode+group_by 路线。

    把每个客户的购买数量收拢成 list 后，用 .list.sum() 就地求和，
    与"explode 再 group_by sum"结果一致，但行数不变、通常更快。
    """
    section("3) .list 就地计算 vs explode 路线")

    orders = pl.read_parquet(ORDERS_PARQUET)
    # 每个客户购买数量收拢成 list（取前 5 个客户）。
    grouped = (
        orders.group_by("customer_id")
        .agg(pl.col("quantity").alias("qtys"))
        .sort("customer_id")
        .head(5)
    )

    # 路线 A：.list 就地计算，行数不变。
    via_list = grouped.select(
        "customer_id",
        pl.col("qtys").list.len().alias("n_orders"),
        pl.col("qtys").list.sum().alias("total_qty"),
        # eval：对 list 内每个元素跑表达式（这里翻倍后求和）。
        pl.col("qtys").list.eval(pl.element() * 2).list.sum().alias("double_sum"),
    )
    show(".list 就地计算", via_list)

    # 路线 B：explode 再聚合，结果的 total_qty 应与 A 一致。
    # empty_as_null=True 显式保持当前行为（未来的 Polars 2.0 起该默认值将改为 False）。
    via_explode = (
        grouped.explode("qtys", empty_as_null=True)
        .group_by("customer_id")
        .agg(pl.col("qtys").sum().alias("total_qty"))
        .sort("customer_id")
    )
    show("explode 路线（total_qty 对照）", via_explode)


def demo_struct_pack_unpack() -> None:
    """演示 .struct 的打包、取字段与 unnest 解包。

    多列打包成一个 struct 列，可取单字段，也可 unnest 展开回多列。
    """
    section("4) .struct 打包/解包")

    df = pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30], "z": ["a", "b", "c"]})

    # 打包：x, y 两列 → 一个 struct 列。
    packed = df.select(pl.struct(["x", "y"]).alias("point"), "z")
    show("打包成 struct 列", packed)

    # 取字段：从 struct 中取出 x。
    show("取出 struct.field('x')", packed.select(pl.col("point").struct.field("x")))

    # unnest：把 struct 展开回多列。
    show("unnest 展开回多列", packed.unnest("point"))


def demo_struct_value_counts() -> None:
    """演示 .struct 承载"一次返回多值"的表达式结果。

    value_counts 返回 struct{值, count}，用 unnest 展开成两列，
    体现 Struct 作为"多值容器"的作用。
    """
    section("5) .struct 承载 value_counts 多值结果")

    orders = pl.read_parquet(ORDERS_PARQUET)
    vc = (
        orders.select(pl.col("channel").value_counts(sort=True))
        .unnest("channel")  # 展开 struct{channel, count}
    )
    show("channel 的 value_counts（unnest 后）", vc)


def demo_cross_namespace() -> None:
    """演示跨命名空间的链式调用（str → list）。

    对脏字符串先清洗、再按逗号拆成 list、取首元素，一条链跨两个命名空间。
    """
    section("6) 跨命名空间链式（str → list）")

    df = pl.DataFrame({"tags": [" a,b,c ", "X,Y", " m "]})
    result = df.select(
        "tags",
        # strip → split(list) → 取第一个元素。
        pl.col("tags").str.strip_chars().str.split(",").list.first().alias("first_tag"),
        # strip → split → list 长度。
        pl.col("tags").str.strip_chars().str.split(",").list.len().alias("n_tags"),
    )
    show("str→list 复合清洗", result)


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_str_cleaning()
    demo_str_regex()
    demo_list_inplace()
    demo_struct_pack_unpack()
    demo_struct_value_counts()
    demo_cross_namespace()


if __name__ == "__main__":
    main()
