"""
01 · 配套代码：数据结构与 Arrow 内存
=====================================================================
配合 src/content/docs/01-data-structures.md 阅读。

逐一验证本节的核心论点：
    1) Series / DataFrame 的构造与 schema
    2) 列式存储的内存测量
    3) null vs NaN 是两回事（两套 API）
    4) 整数列带 null 不退化为 float（对照 pandas 会退化）
    5) chunk 与 rechunk
    6) 与 Apache Arrow 的零拷贝互通
    7) Polars 没有 index：用位置而非标签定位行

运行：
    uv run code/01_data_structures.py
"""

from __future__ import annotations

import pandas as pd
import polars as pl

from _common import section, show


def demo_construct() -> None:
    """演示 Series / DataFrame 的构造，并查看类型系统。

    重点：DataFrame 是"一组等长的 Series"，每列有明确 dtype，
    String 是原生类型而非 pandas 的 object。
    """
    section("1) 构造 Series / DataFrame，查看 schema")

    # Series：一列同类型数据，是 Polars 的基本单位。
    s = pl.Series("nums", [10, 20, 30])
    show("Series", s)
    show("Series dtype", s.dtype)

    # DataFrame：多列等长 Series 的集合。
    df = pl.DataFrame(
        {
            "order_id": [1, 2, 3],
            "city": ["Beijing", "Shanghai", "Shenzhen"],
            "revenue": [12.5, 8.0, 30.2],
        }
    )
    show("DataFrame", df)
    # schema 是"列名 → dtype"的映射，注意 city 是 String 而非 object。
    show("schema", df.schema)


def demo_memory() -> None:
    """测量列式存储的真实内存占用。

    estimated_size('b') 返回该结构占用的字节数，
    让"列式 = 一段连续字节"这件事变得可量化。
    """
    section("2) 列式存储的内存测量")

    # 100 万个 Int64，理论上约 8 字节 × 1e6 ≈ 8 MB。
    big = pl.Series("x", range(1_000_000))
    show("100 万 Int64 的字节数", f"{big.estimated_size('mb'):.2f} MB")
    show("单元素平均字节", big.estimated_size("b") / len(big))


def demo_null_vs_nan() -> None:
    """演示 null 与 NaN 是两个不同概念，需用两套不同 API。

    - null：值缺失，用 is_null / fill_null 处理。
    - NaN ：浮点运算的非数结果，用 is_nan / fill_nan 处理。
    二者混用会得到错误结果，这是 pandas 迁移者最常踩的坑。
    """
    section("3) null vs NaN：两个概念，两套 API")

    # 同时包含 null（缺失）与 NaN（0/0 的浮点非数）。
    df = pl.DataFrame({"v": [1.0, None, float("nan"), 4.0]})
    show("原始数据（含 null 和 NaN）", df)

    # 分别用两套 API 探测，注意结果的差异。
    result = df.with_columns(
        pl.col("v").is_null().alias("is_null"),   # 只标记缺失
        pl.col("v").is_nan().alias("is_nan"),     # 只标记浮点非数
    )
    show("is_null 只命中缺失，is_nan 只命中 NaN", result)

    # fill_null 只填缺失，NaN 原样保留；fill_nan 反之。
    show("fill_null(-1) 只改 null", df.with_columns(pl.col("v").fill_null(-1)))
    show("fill_nan(-2) 只改 NaN", df.with_columns(pl.col("v").fill_nan(-2)))


def demo_int_null_no_upcast() -> None:
    """证明整数列带 null 不会退化为 float（对照 pandas 传统行为）。

    Polars 借助 Arrow 的 validity bitmap 记录缺失，值本身仍是整数；
    而 pandas 传统的 int 列一旦有缺失就被迫转成 float64。
    """
    section("4) 整数列带 null 不退化为 float")

    pl_s = pl.Series("i", [1, 2, None, 4])
    # 依旧是 Int64，缺失存在旁路 bitmap，不影响值的类型。
    show("Polars: Int64 带 null 仍是", pl_s.dtype)

    # 对照 pandas 传统 Series：int + 缺失 → float64。
    pd_s = pd.Series([1, 2, None, 4])
    show("pandas: int 带缺失退化为", pd_s.dtype)


def demo_chunks() -> None:
    """演示 ChunkedArray 的分块特性与 rechunk 合并。

    concat 会把两段内存"逻辑拼接"而非物理复制，因此 chunk 数增加；
    rechunk() 把它们合并为单块连续内存，利于后续顺序访问。
    """
    section("5) chunk 与 rechunk")

    a = pl.Series("a", [1, 2, 3])
    b = pl.Series("a", [4, 5, 6])
    joined = pl.concat([a, b], rechunk=False)  # 刻意不合并，观察 chunk 数
    show("concat 后 n_chunks", joined.n_chunks())
    show("rechunk() 后 n_chunks", joined.rechunk().n_chunks())


def demo_arrow_interop() -> None:
    """演示 Polars 与 Apache Arrow 的互通。

    Polars 本质是 Arrow 内存之上的计算引擎，因此与 Arrow 互转开销极低，
    也能借此与其他 Arrow 生态工具（pandas 2.0+ / DuckDB / Parquet）无缝衔接。
    """
    section("6) 与 Apache Arrow 互通")

    df = pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    arrow_table = df.to_arrow()  # 转为 pyarrow.Table
    show("to_arrow() 得到的类型", type(arrow_table))

    # 再从 Arrow 转回 Polars，数据无损往返。
    back = pl.from_arrow(arrow_table)
    show("from_arrow() 转回 Polars", back)


def demo_no_index() -> None:
    """演示 Polars 没有 index：行的身份就是它的位置。

    对照 pandas 的 df.loc[label]，Polars 没有隐式行标签，
    定位行用位置（如 df[2] / slice）或用表达式过滤（filter），
    这消除了 pandas 中大量隐式对齐带来的意外。
    """
    section("7) Polars 没有 index，用位置/过滤定位行")

    df = pl.DataFrame({"name": ["a", "b", "c", "d"], "score": [90, 85, 70, 60]})
    # 按位置取第 3 行（下标 2），不存在"标签"。
    show("按位置取第 3 行 df[2]", df[2])
    # 更地道的做法：用表达式过滤，而非依赖行标签。
    show("按条件过滤 score > 80", df.filter(pl.col("score") > 80))


def main() -> None:
    """依次运行全部演示。"""
    demo_construct()
    demo_memory()
    demo_null_vs_nan()
    demo_int_null_no_upcast()
    demo_chunks()
    demo_arrow_interop()
    demo_no_index()


if __name__ == "__main__":
    main()
