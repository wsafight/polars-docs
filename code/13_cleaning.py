"""
13 · 配套代码：数据清洗与准备
=====================================================================
配合 src/content/docs/13-cleaning.md 阅读。

用第 00 节注入脏料的真实数据集，按清洗流水线顺序演示：
    1) 审查脏数据（null 数、重复行）——先体检
    2) 去重（unique / is_duplicated）
    3) 类型修复（cast + strict）
    4) 字符串归一化（衔接第 08 节）
    5) 缺失值策略（drop + 常数/forward/backward/interpolate/中位数 5 种填充）
    6) 异常值处理（filter 删除 vs clip 裁剪）
    7) 完整清洗管道（Lazy，为第 14 节铺垫）

运行：
    uv run code/13_cleaning.py
"""

from __future__ import annotations

import polars as pl

from _common import ORDERS_PARQUET, ensure_data_exists, section, show


def demo_inspect() -> None:
    """审查脏数据：统计各列 null 数与重复行数。

    清洗前先"体检"——不了解脏在哪，就无从下手。
    """
    section("1) 审查脏数据（先体检）")

    orders = pl.read_parquet(ORDERS_PARQUET)
    show("总行数", orders.height)
    # null_count 一次性给出每列的缺失数量。
    show("各列 null 数", orders.null_count())
    # 整行完全重复的行数 = 总行数 - 去重后行数。
    show("重复行数", orders.height - orders.unique().height)


def demo_dedup() -> None:
    """演示去重与重复检测。

    unique 删除第 00 节注入的 20 条重复行；is_duplicated 只标记不删除，
    便于先审查再决定。
    """
    section("2) 去重（unique / is_duplicated）")

    orders = pl.read_parquet(ORDERS_PARQUET)

    deduped = orders.unique()
    show("全列去重后行数（5020→5000）", deduped.height)

    # 按业务主键 order_id 去重，保留首次出现。
    by_key = orders.unique(subset=["order_id"], keep="first")
    show("按 order_id 去重后行数", by_key.height)

    # is_duplicated 检测：标记哪些行是重复的（不删除）。
    dup_count = orders.filter(pl.col("order_id").is_duplicated()).height
    show("被标记为重复 order_id 的行数", dup_count)


def demo_cast() -> None:
    """演示 cast 类型修复与 strict 模式差异。

    模拟从 CSV 读入的字符串列，分别用 strict=True/False 转数值，
    展示"报错 fail fast" vs "脏值降级为 null"两种边界策略。
    """
    section("3) 类型修复（cast + strict）")

    # 模拟脏的字符串数值列（含一个无法转换的 'N/A'）。
    df = pl.DataFrame({"amount_str": ["100", "200", "N/A", "400"]})

    # strict=False：转不动的 'N/A' 变成 null。
    lenient = df.with_columns(
        pl.col("amount_str").cast(pl.Int64, strict=False).alias("amount")
    )
    show("strict=False（'N/A'→null）", lenient)

    # strict=True：遇到 'N/A' 直接报错（契约边界 fail fast）。
    try:
        df.with_columns(pl.col("amount_str").cast(pl.Int64, strict=True))
    except Exception as exc:  # noqa: BLE001  # 教学用途
        show("strict=True 主动报错", f"{type(exc).__name__}")


def demo_string_normalize() -> None:
    """演示字符串归一化（衔接第 08 节）。

    把脏的 note 列 strip + lower，让 ' OK '/'Gift ' 等脏值归并。
    """
    section("4) 字符串归一化")

    orders = pl.read_parquet(ORDERS_PARQUET)
    cleaned = orders.with_columns(
        pl.col("note").str.strip_chars().str.to_lowercase().alias("note_clean")
    )
    # 归一化后不同写法被合并，唯一值数量下降。
    show("归一化前 note 唯一值数", orders.select(pl.col("note").n_unique()).item())
    show("归一化后 note 唯一值数", cleaned.select(pl.col("note_clean").n_unique()).item())


def demo_missing_strategies() -> None:
    """演示缺失值的处理策略，对照效果。

    对含 null 的序列分别用常数 / forward / backward / interpolate / 中位数
    这 5 种填充策略，外加会改变行数的 drop，直观看到每种策略产出的差异，
    便于按数据性质选择。
    """
    section("5) 缺失值策略（5 种填充 + drop）")

    # 构造一个有序含 null 的序列（模拟时间序列里的缺失）。
    df = pl.DataFrame({"v": [10.0, None, None, 40.0, 50.0, None, 70.0]})
    show("原始（含 null）", df)

    result = df.with_columns(
        pl.col("v").fill_null(0).alias("const_0"),                    # 常数
        pl.col("v").fill_null(strategy="forward").alias("forward"),   # 前值延续
        pl.col("v").fill_null(strategy="backward").alias("backward"), # 后值延续
        pl.col("v").interpolate().alias("interpolate"),               # 线性插值
        pl.col("v").fill_null(pl.col("v").median()).alias("median"),  # 中位数
    )
    show("5 种填充策略对照", result)

    # drop 策略单独展示（会改变行数）。
    show("drop_nulls（行数变少）", df.drop_nulls())


def demo_outliers() -> None:
    """演示异常值处理的两种哲学：filter 删除 vs clip 裁剪。

    折扣的合理范围是 [0, 1]。构造若干越界值，
    分别用 filter（视为错误、删行）和 clip（视为极端、拉回边界）处理。
    """
    section("6) 异常值：filter 删除 vs clip 裁剪")

    df = pl.DataFrame({"discount": [-0.2, 0.1, 0.5, 0.9, 1.5, 0.3]})
    show("原始（含越界值 -0.2 和 1.5）", df)

    # 哲学 A：删除越界行。
    filtered = df.filter(pl.col("discount").is_between(0, 1))
    show("filter 删除越界（6→4 行）", filtered)

    # 哲学 B：裁剪到 [0,1]，保留所有行。
    clipped = df.with_columns(pl.col("discount").clip(0, 1))
    show("clip 裁剪到边界（保留 6 行）", clipped)


def demo_full_pipeline() -> None:
    """把清洗步骤串成一个 Lazy 管道，输出干净数据。

    顺序：去重 → 清洗字符串 → 处理缺失（折扣缺失填 0）→ 过滤异常，
    这为第 14 节的端到端实战提供"干净数据"起点。
    """
    section("7) 完整清洗管道（Lazy）")

    clean = (
        pl.scan_parquet(ORDERS_PARQUET)
        .unique()                                                     # ① 去重
        .with_columns(
            pl.col("note").str.strip_chars().str.to_lowercase().alias("note")  # ③ 洗字符串
        )
        .with_columns(pl.col("discount").fill_null(0.0))              # ④ 缺失填 0
        .filter(pl.col("discount").is_between(0, 1))                  # ⑤ 过滤异常
        .collect()
    )
    show("清洗后行数", clean.height)
    show("清洗后 discount 已无 null", clean.null_count().select("discount"))
    show("预览", clean.head(5))


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_inspect()
    demo_dedup()
    demo_cast()
    demo_string_normalize()
    demo_missing_strategies()
    demo_outliers()
    demo_full_pipeline()


if __name__ == "__main__":
    main()
