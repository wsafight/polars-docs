"""
11 · 配套代码：性能剖析与最佳实践
=====================================================================
配合 src/content/docs/11-performance.md 阅读。

用真实 benchmark 兑现"更快"的论断：
    1) 公平拆分基准：纯内存计算 vs 文件扫描 + 计算
    2) map_elements vs 原生表达式（量化翻译腔代价）
    3) 一次性 vs 多次 with_columns
    4) explain 定位瓶颈
    5) dtype 对内存的影响

注意：本脚本生成 300 万行临时数据并多轮计时，运行需数秒，属正常。

运行：
    uv run code/11_performance.py
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable

import duckdb
import numpy as np
import pandas as pd
import polars as pl

from _common import DATA_DIR, section, show

# benchmark 数据规模。
N_ROWS = 3_000_000
BENCH_PARQUET = DATA_DIR / "_bench.parquet"


def timeit(fn: Callable[[], object], repeat: int = 3) -> float:
    """对一个无参函数多轮计时，返回最优耗时（毫秒）。

    取多轮最小值而非平均，以减少系统抖动/GC 的偶发干扰，
    这是微基准测试的常见做法。

    参数:
        fn:     待测的无参可调用对象。
        repeat: 重复轮数。
    返回:
        最优单轮耗时，单位毫秒。
    """
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best * 1000


def make_bench_data() -> pl.DataFrame:
    """生成 300 万行基准数据并落盘 Parquet（供 Lazy/DuckDB 读取）。

    返回:
        内存中的基准 DataFrame（供 pandas/Eager 使用）。
    """
    rng = np.random.default_rng(0)
    df = pl.DataFrame(
        {
            "key": rng.integers(0, 1000, N_ROWS),
            "cat": rng.choice(["a", "b", "c", "d"], N_ROWS),
            "v1": rng.random(N_ROWS),
            "v2": rng.random(N_ROWS),
        }
    )
    df.write_parquet(BENCH_PARQUET)
    return df


def show_relative(results: dict[str, float], baseline: str) -> None:
    """打印一组耗时及其相对指定基线的倍率。"""
    base = results[baseline]
    for name, ms in results.items():
        show(name, f"{ms:6.1f} ms   （相对 {baseline} {base / ms:.1f}x）")


def demo_benchmarks(df: pl.DataFrame) -> None:
    """分开测量纯内存计算与文件扫描，避免把 IO 条件混在一起。

    两组都执行同一任务：过滤 v1>0.5 后按 cat 聚合 v2。

    参数:
        df: 内存中的基准数据。
    """
    section("1A) 纯内存计算（pandas vs Polars Eager）")

    pdf = df.to_pandas()

    def pandas_task() -> object:
        """pandas 实现：布尔索引过滤 + groupby 聚合。"""
        d = pdf[pdf["v1"] > 0.5]
        return d.groupby("cat")["v2"].agg(["mean", "count"])

    def polars_eager() -> object:
        """Polars Eager 实现：内存数据直接过滤聚合。"""
        return df.filter(pl.col("v1") > 0.5).group_by("cat").agg(
            pl.col("v2").mean(), pl.len()
        )

    memory_results = {
        "pandas": timeit(pandas_task),
        "polars eager": timeit(polars_eager),
    }
    show_relative(memory_results, baseline="pandas")

    section("1B) 文件扫描 + 计算（四方均含 Parquet IO）")

    def pandas_scan() -> object:
        """pandas：读取任务所需列后过滤聚合。"""
        scanned = pd.read_parquet(BENCH_PARQUET, columns=["cat", "v1", "v2"])
        selected = scanned[scanned["v1"] > 0.5]
        return selected.groupby("cat")["v2"].agg(["mean", "count"])

    def polars_read() -> object:
        """Polars Eager：读取任务所需列后过滤聚合。"""
        scanned = pl.read_parquet(BENCH_PARQUET, columns=["cat", "v1", "v2"])
        return scanned.filter(pl.col("v1") > 0.5).group_by("cat").agg(
            pl.col("v2").mean(), pl.len()
        )

    def polars_scan() -> object:
        """Polars Lazy：从 Parquet 惰性扫描并下推投影/过滤。"""
        return (
            pl.scan_parquet(BENCH_PARQUET)
            .filter(pl.col("v1") > 0.5)
            .group_by("cat")
            .agg(pl.col("v2").mean(), pl.len())
            .collect()
        )

    def duck_task() -> object:
        """DuckDB 实现：直接查询 Parquet。"""
        return duckdb.sql(
            f"SELECT cat, avg(v2), count(*) FROM read_parquet('{BENCH_PARQUET}') "
            f"WHERE v1 > 0.5 GROUP BY cat"
        ).pl()

    scan_results = {
        "pandas read+compute": timeit(pandas_scan),
        "polars read+compute": timeit(polars_read),
        "polars scan+compute": timeit(polars_scan),
        "duckdb": timeit(duck_task),
    }
    show_relative(scan_results, baseline="pandas read+compute")


def demo_apply_penalty() -> None:
    """量化 map_elements（逐行回调 Python）相对原生表达式的代价。

    同一计算用两种方式实现并计时，直观看到"翻译腔"慢一个数量级以上。
    """
    section("2) map_elements vs 原生表达式")

    df = pl.DataFrame({"x": np.arange(1_000_000)})

    def with_map_elements() -> object:
        """反例：逐元素回调 Python 函数。"""
        # map_elements 必然触发 PolarsInefficientMapWarning，此处正是要量化它的代价，
        # 属预期行为，局部抑制以免每轮计时都刷屏。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", pl.exceptions.PolarsInefficientMapWarning)
            return df.select(
                pl.col("x").map_elements(lambda v: v * 2 + 1, return_dtype=pl.Int64)
            )

    def with_native() -> object:
        """正例：原生向量化表达式。"""
        return df.select(pl.col("x") * 2 + 1)

    t_map = timeit(with_map_elements, repeat=2)
    t_native = timeit(with_native, repeat=2)
    show("map_elements 耗时", f"{t_map:.1f} ms")
    show("原生表达式耗时", f"{t_native:.1f} ms")
    show("原生比 map_elements 快", f"{t_map / t_native:.0f}x")


def demo_with_columns_batching() -> None:
    """对比"一次 with_columns 塞多表达式"与"多次 with_columns"。

    一次性写法让 Polars 并行计算各表达式，通常优于链式多次调用。
    """
    section("3) 一次性 vs 多次 with_columns")

    rng = np.random.default_rng(1)
    df = pl.DataFrame({"a": rng.random(1_000_000), "b": rng.random(1_000_000)})

    def batched() -> object:
        """一次 with_columns 计算 4 个派生列（可并行）。"""
        return df.with_columns(
            (pl.col("a") + pl.col("b")).alias("s"),
            (pl.col("a") - pl.col("b")).alias("d"),
            (pl.col("a") * pl.col("b")).alias("p"),
            (pl.col("a") / pl.col("b")).alias("q"),
        )

    def chained() -> object:
        """链式 4 次 with_columns（每次一个列）。"""
        return (
            df.with_columns((pl.col("a") + pl.col("b")).alias("s"))
            .with_columns((pl.col("a") - pl.col("b")).alias("d"))
            .with_columns((pl.col("a") * pl.col("b")).alias("p"))
            .with_columns((pl.col("a") / pl.col("b")).alias("q"))
        )

    show("一次性 with_columns", f"{timeit(batched):.1f} ms")
    show("链式多次 with_columns", f"{timeit(chained):.1f} ms")


def demo_profile() -> None:
    """演示如何定位瓶颈：用 explain 查看优化计划与执行策略。

    注：LazyFrame.profile 能给出各算子耗时，但在流式/多线程并发执行下，
    单算子墙钟耗时可能相互重叠而具有误导性；这里改用 explain 观察计划
    （下推/流式节点），再配合 timeit 对可疑写法做 A/B 计时，是更稳妥的剖析方式。
    """
    section("4) 用 explain 定位瓶颈")

    lf = (
        pl.scan_parquet(BENCH_PARQUET)
        .filter(pl.col("v1") > 0.3)
        .group_by("cat")
        .agg(pl.col("v2").sum())
    )
    # explain 显示优化后的物理计划：确认过滤下推、投影裁剪是否生效。
    show("优化计划", lf.explain())
    # 对整条管道计时，作为该写法的性能基线。
    show("该管道耗时", f"{timeit(lambda: lf.collect()):.1f} ms")


def demo_dtype_memory() -> None:
    """演示 dtype 选择对内存的影响：Int64 vs Int32 vs Categorical。

    更窄的类型和类别编码显著省内存，间接提升缓存命中与速度。
    """
    section("5) dtype 对内存的影响")

    n = 1_000_000
    rng = np.random.default_rng(2)
    ints = rng.integers(0, 100, n)
    cats = rng.choice(["alpha", "beta", "gamma", "delta"], n)

    as_i64 = pl.Series("x", ints, dtype=pl.Int64)
    as_i32 = pl.Series("x", ints, dtype=pl.Int32)
    as_str = pl.Series("c", cats, dtype=pl.String)
    as_cat = pl.Series("c", cats, dtype=pl.Categorical)

    show("Int64 体积", f"{as_i64.estimated_size('mb'):.2f} MB")
    show("Int32 体积（省一半）", f"{as_i32.estimated_size('mb'):.2f} MB")
    show("String 体积", f"{as_str.estimated_size('mb'):.2f} MB")
    show("Categorical 体积（低基数更省）", f"{as_cat.estimated_size('mb'):.2f} MB")


def main() -> None:
    """生成基准数据、依次运行全部演示，最后清理临时文件。"""
    df = make_bench_data()
    try:
        demo_benchmarks(df)
        demo_apply_penalty()
        demo_with_columns_batching()
        demo_profile()
        demo_dtype_memory()
    finally:
        # 无论成功与否都清理临时大文件。
        if BENCH_PARQUET.exists():
            BENCH_PARQUET.unlink()


if __name__ == "__main__":
    main()
