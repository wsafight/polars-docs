"""
10 · 配套代码：IO 与流式引擎
=====================================================================
配合 src/content/docs/10-io-streaming.md 阅读。

演示：
    1) read vs scan 返回类型与下推
    2) Parquet / CSV / NDJSON 格式往返与体积对比
    3) CSV 类型推断的坑（对照 Parquet 无损）
    4) streaming 流式聚合
    5) sink_parquet 流式写出
    6) DuckDB 直读 Parquet（对照 scan）

运行：
    uv run code/10_io_streaming.py
"""

from __future__ import annotations

import duckdb
import polars as pl

from _common import DATA_DIR, ORDERS_PARQUET, ensure_data_exists, section, show


def demo_read_vs_scan() -> None:
    """对比 read（急切）与 scan（惰性）的返回类型及 scan 的下推能力。"""
    section("1) read vs scan")

    eager = pl.read_parquet(ORDERS_PARQUET)
    lazy = pl.scan_parquet(ORDERS_PARQUET).select("order_id", "quantity")

    show("read_parquet → 类型", type(eager))
    show("scan_parquet → 类型", type(lazy))
    # scan + select 后，优化计划只投影 2 列。
    show("scan 优化计划（投影下推）", lazy.explain())


def demo_format_roundtrip() -> None:
    """演示三种格式的写读往返，并对比文件体积。

    同一份订单数据分别写成 Parquet / CSV / NDJSON，比较磁盘占用，
    直观看到列式压缩的 Parquet 通常最小。
    """
    section("2) 格式往返与体积对比")

    df = pl.read_parquet(ORDERS_PARQUET)

    pq = DATA_DIR / "_rt.parquet"
    csv = DATA_DIR / "_rt.csv"
    ndjson = DATA_DIR / "_rt.ndjson"

    df.write_parquet(pq)
    df.write_csv(csv)
    df.write_ndjson(ndjson)

    # 读回验证行数一致。
    show("Parquet 读回行数", pl.read_parquet(pq).height)
    show("CSV 读回行数", pl.read_csv(csv).height)
    show("NDJSON 读回行数", pl.read_ndjson(ndjson).height)

    # 对比体积（KB）。
    for path in (pq, csv, ndjson):
        show(f"{path.name} 体积", f"{path.stat().st_size / 1024:.1f} KB")

    # 清理临时文件。
    for path in (pq, csv, ndjson):
        path.unlink()


def demo_csv_type_pitfall() -> None:
    """演示 CSV 无类型的坑：日期/时间戳读回可能变字符串，Parquet 则无损。

    CSV 不保存 schema，读回依赖推断；Parquet 内嵌 schema，类型完全保留。
    """
    section("3) CSV 类型推断的坑 vs Parquet 无损")

    original = pl.read_parquet(ORDERS_PARQUET).select("order_id", "order_ts", "discount")
    show("原始 schema（order_ts 是 datetime）", original.schema)

    # 写成 CSV 再读回：默认不解析日期，order_ts 变字符串。
    csv = DATA_DIR / "_types.csv"
    original.write_csv(csv)
    csv_back = pl.read_csv(csv)
    show("CSV 读回 schema（order_ts 退化为 str）", csv_back.schema)

    # 写成 Parquet 再读回：schema 完全保留。
    pq = DATA_DIR / "_types.parquet"
    original.write_parquet(pq)
    pq_back = pl.read_parquet(pq)
    show("Parquet 读回 schema（无损）", pq_back.schema)

    csv.unlink()
    pq.unlink()


def demo_streaming() -> None:
    """演示 streaming 流式引擎执行聚合。

    engine='streaming' 让查询分批执行并降低峰值内存。它不保证恒定内存：
    聚合状态、阻塞算子和 collect 后的结果仍可能随数据规模增长。
    """
    section("4) streaming 流式聚合")

    result = (
        pl.scan_parquet(ORDERS_PARQUET)
        .filter(pl.col("quantity") > 2)
        .group_by("channel")
        .agg(pl.len().alias("n"), pl.col("quantity").sum().alias("total_qty"))
        .sort("n", descending=True)
        .collect(engine="streaming")  # 流式执行
    )
    show("流式引擎聚合结果", result)


def demo_sink() -> None:
    """演示 sink_parquet 流式写出：scan → 变换 → sink，不物化完整结果表。

    这是大文件 ETL 的常用形态：输入惰性扫描、输出流式落盘。
    """
    section("5) sink_parquet 流式写出")

    out = DATA_DIR / "_sink.parquet"
    (
        pl.scan_parquet(ORDERS_PARQUET)
        .filter(pl.col("discount").is_not_null())
        .select("order_id", "channel", "discount")
        .sink_parquet(out)  # 边算边写，不 collect 整表
    )
    # 验证写出结果。
    show("sink 写出的行数", pl.scan_parquet(out).select(pl.len()).collect().item())
    out.unlink()


def demo_duckdb_scan() -> None:
    """对照：DuckDB 直接查询 Parquet 文件（无需先加载）。

    与 Polars 的 scan 一样，DuckDB 也把过滤下推到 Parquet 读取，
    二者基于同一套 Arrow，可零拷贝互通。
    """
    section("6) DuckDB 直读 Parquet（对照 scan）")

    sql = f"""
        SELECT channel, COUNT(*) AS n
        FROM read_parquet('{ORDERS_PARQUET}')
        WHERE quantity > 2
        GROUP BY channel
        ORDER BY n DESC
    """
    show("DuckDB 结果（与 streaming 一致）", duckdb.sql(sql).pl())


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_read_vs_scan()
    demo_format_roundtrip()
    demo_csv_type_pitfall()
    demo_streaming()
    demo_sink()
    demo_duckdb_scan()


if __name__ == "__main__":
    main()
