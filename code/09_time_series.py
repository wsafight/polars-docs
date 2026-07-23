"""
09 · 配套代码：时间序列处理
=====================================================================
配合 src/content/docs/09-time-series.md 阅读。

演示时间处理三层次：
    1) .dt 提取/截断
    2) 手动重采样（truncate + group_by）
    3) group_by_dynamic 按周/月重采样（对照 pandas resample）
    4) 分组重采样（每渠道每周）
    5) rolling 按行数移动平均
    6) rolling 按时间窗口（过去 7 天）

运行：
    uv run code/09_time_series.py
"""

from __future__ import annotations

import polars as pl

from _common import ORDERS_PARQUET, PRODUCTS_PARQUET, ensure_data_exists, section, show

REVENUE = (
    pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
).alias("revenue")


def load_ts() -> pl.DataFrame:
    """加载订单并 join 单价、附加 revenue，按时间排序。

    时间序列操作（group_by_dynamic / rolling）要求按时间列有序，
    因此这里统一 sort，后续演示直接复用。

    返回:
        按 order_ts 升序、含 revenue 的订单表。
    """
    return (
        pl.read_parquet(ORDERS_PARQUET)
        .join(pl.read_parquet(PRODUCTS_PARQUET), on="product_id")
        .with_columns(REVENUE)
        .sort("order_ts")
    )


def demo_dt_extract() -> None:
    """演示 .dt 命名空间：提取时间成分并截断到天。"""
    section("1) .dt 提取/截断")

    df = load_ts()
    result = df.select(
        "order_ts",
        pl.col("order_ts").dt.year().alias("year"),
        pl.col("order_ts").dt.month().alias("month"),
        pl.col("order_ts").dt.weekday().alias("weekday"),  # 1=周一
        pl.col("order_ts").dt.hour().alias("hour"),
        pl.col("order_ts").dt.truncate("1d").alias("day_bucket"),  # 对齐到当天 0 点
    )
    show(".dt 提取结果", result.head(5))


def demo_manual_resample() -> None:
    """演示"手动重采样"：先 truncate 到天，再普通 group_by 聚合。

    这揭示了重采样的本质——把每条记录对齐到某粒度，再分组聚合。
    """
    section("2) 手动重采样（truncate + group_by）")

    df = load_ts()
    daily = (
        df.with_columns(pl.col("order_ts").dt.truncate("1d").alias("day"))
        .group_by("day")
        .agg(pl.len().alias("n"), pl.col("revenue").sum().round(2).alias("total"))
        .sort("day")
    )
    show("按天聚合（手动）", daily.head(5))


def demo_group_by_dynamic() -> None:
    """演示 group_by_dynamic 按周、按月重采样，并对照 pandas resample。"""
    section("3) group_by_dynamic 重采样（对照 pandas）")

    df = load_ts()

    # 按周重采样。
    weekly = (
        df.group_by_dynamic("order_ts", every="1w")
        .agg(pl.len().alias("n"), pl.col("revenue").sum().round(2).alias("total"))
    )
    show("Polars 按周", weekly.head(5))

    # 按月重采样（日历感知，正确处理不同月份天数）。
    monthly = (
        df.group_by_dynamic("order_ts", every="1mo")
        .agg(pl.col("revenue").sum().round(2).alias("total"))
    )
    show("Polars 按月", monthly)

    # pandas 对照：resample 依赖 DatetimeIndex。
    pdf = df.select("order_ts", "revenue").to_pandas().set_index("order_ts")
    pd_weekly = pdf.resample("W").agg(n=("revenue", "size"), total=("revenue", "sum"))
    show("pandas resample('W')（前 5 行）", pd_weekly.head(5).round(2))


def demo_grouped_resample() -> None:
    """演示分组重采样：每个渠道每周的销售额。

    group_by 参数让 group_by_dynamic 在每个类别内分别按时间分桶。
    """
    section("4) 分组重采样（每渠道每周）")

    df = load_ts()
    result = (
        df.group_by_dynamic("order_ts", every="1w", group_by="channel")
        .agg(pl.col("revenue").sum().round(2).alias("total"))
        .sort("channel", "order_ts")
    )
    show("每渠道每周销售额", result.head(8))


def demo_rolling_by_rows() -> None:
    """演示按行数的滑动窗口：7 单移动平均。

    rolling_mean(window_size=7) 取前 7 行求均值，适合等间隔序列。
    前 6 行不足窗口，结果为 null。
    """
    section("5) rolling 按行数（7 单移动平均）")

    df = load_ts().head(200)
    result = df.select(
        "order_ts",
        "revenue",
        pl.col("revenue").rolling_mean(window_size=7).round(2).alias("ma7"),
    )
    show("7 单移动平均", result.head(10))


def demo_rolling_by_time() -> None:
    """演示按时间的滑动窗口：过去 7 天的销售额合计。

    rolling(period='7d') 按真实时间取窗口，即使订单在时间上疏密不均也正确，
    这是不规则时间戳场景的推荐做法。
    """
    section("6) rolling 按时间（过去 7 天销售额）")

    df = load_ts()
    result = (
        df.rolling(index_column="order_ts", period="7d")
        .agg(
            pl.len().alias("n_in_7d"),
            pl.col("revenue").sum().round(2).alias("rev_7d"),
        )
    )
    show("过去 7 天滑动合计", result.head(8))


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_dt_extract()
    demo_manual_resample()
    demo_group_by_dynamic()
    demo_grouped_resample()
    demo_rolling_by_rows()
    demo_rolling_by_time()


if __name__ == "__main__":
    main()
