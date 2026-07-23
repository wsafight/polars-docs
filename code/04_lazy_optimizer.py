"""
04 · 配套代码：惰性执行与查询优化器
=====================================================================
配合 src/content/docs/04-lazy-optimizer.md 阅读。

演示 Polars 的"大脑"：
    1) scan（惰性）vs read（急切）的返回类型差异
    2) explain 对照：同一查询"优化前 vs 优化后"的计划
    3) 投影下推证据（PROJECT n/8 COLUMNS）
    4) 谓词下推证据（SELECTION 出现在 SCAN 层）
    5) collect 触发执行
    6) 切片下推（head 被下推）

运行：
    uv run code/04_lazy_optimizer.py
"""

from __future__ import annotations

import polars as pl

from _common import ORDERS_PARQUET, ensure_data_exists, section, show


def demo_scan_vs_read() -> None:
    """演示 scan_*（惰性）与 read_*（急切）的本质区别。

    read_parquet 立即把数据加载为 DataFrame；
    scan_parquet 只登记数据来源，返回 LazyFrame，此刻不读任何数据。
    """
    section("1) scan（惰性）vs read（急切）")

    eager = pl.read_parquet(ORDERS_PARQUET)
    lazy = pl.scan_parquet(ORDERS_PARQUET)

    show("read_parquet 返回类型（已加载）", type(eager))
    show("scan_parquet 返回类型（仅计划）", type(lazy))


def build_query() -> pl.LazyFrame:
    """构造一个用于演示优化的查询：过滤 + 投影。

    只关心 quantity>3 的订单的 order_id 和 quantity 两列。
    故意先 filter 再 select，观察优化器如何把它们下推到扫描层。

    返回:
        尚未执行的 LazyFrame（查询计划）。
    """
    return (
        pl.scan_parquet(ORDERS_PARQUET)
        .filter(pl.col("quantity") > 3)
        .select("order_id", "quantity")
    )


def demo_explain() -> None:
    """对照打印查询"优化前 vs 优化后"的执行计划。

    优化前：忠实反映书写顺序（SCAN 全 8 列 → FILTER → SELECT）。
    优化后：坍缩为单次 SCAN，只读 2 列并在扫描时过滤。
    """
    section("2) explain 对照：优化前 vs 优化后")

    lf = build_query()

    print("\n--- 优化前（你写的顺序）---")
    print(lf.explain(optimized=False))

    print("\n--- 优化后（优化器重排）---")
    print(lf.explain(optimized=True))


def demo_pushdown_evidence() -> None:
    """从优化后的计划文本中提取"下推"证据，量化优化效果。

    - 投影下推：计划里出现 'PROJECT 2/8 COLUMNS'（8 列只读 2 列）。
    - 谓词下推：计划里出现 'SELECTION'（过滤进入扫描阶段）。
    """
    section("3+4) 下推证据（投影下推 + 谓词下推）")

    plan_text = build_query().explain(optimized=True)
    show("优化后计划全文", plan_text)

    has_projection = "PROJECT 2/8" in plan_text
    has_selection = "SELECTION" in plan_text
    show("投影下推生效（只读 2/8 列）", has_projection)
    show("谓词下推生效（过滤进入 SCAN）", has_selection)


def demo_collect_triggers() -> None:
    """演示只有 collect 才真正执行并产出数据。

    在 collect 之前，LazyFrame 始终是"计划"；collect 之后才得到 DataFrame。
    """
    section("5) collect 触发执行")

    lf = build_query()
    show("collect 前的类型", type(lf))

    result = lf.collect()
    show("collect 后的类型", type(result))
    show("结果预览", result.head(5))
    show("满足 quantity>3 的行数", result.height)


def demo_slice_pushdown() -> None:
    """演示切片下推：head(n) 被推到扫描层，避免物化整表。

    优化后的计划中，limit/slice 信息会体现在扫描节点上，
    使"取前 n 行"几乎不受总行数影响。
    """
    section("6) 切片下推（head 被下推）")

    lf = pl.scan_parquet(ORDERS_PARQUET).select("order_id", "channel").head(5)
    print(lf.explain(optimized=True))
    show("结果", lf.collect())


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_scan_vs_read()
    demo_explain()
    demo_pushdown_evidence()
    demo_collect_triggers()
    demo_slice_pushdown()


if __name__ == "__main__":
    main()
