"""
15 · 配套代码：UDF 逃生舱与生态互操作
=====================================================================
配合 src/content/docs/15-udf-interop.md 阅读。保持零额外依赖（仅用已装的 numpy）。

演示：
    1) 降级阶梯对照（内置表达式 / map_batches / map_elements 结果一致）
    2) map_batches + numpy 整列向量化
    3) struct 传多列给 UDF
    4) map_elements 止损（return_dtype + 先过滤再 map）
    5) numpy 互转（to_numpy / from_numpy）
    6) 文本可视化（聚合结果 → ASCII 条形图）

运行：
    uv run code/15_udf_interop.py
"""

from __future__ import annotations

import warnings

import numpy as np
import polars as pl

from _common import ORDERS_PARQUET, PRODUCTS_PARQUET, ensure_data_exists, section, show


def demo_ladder() -> None:
    """降级阶梯对照：同一变换 y = x^2 + 1 用三种写法，验证结果一致。

    三种写法结果相同，但性能与可优化性从上到下递减（第11节已量化差距）。
    """
    section("1) 降级阶梯对照（三种写法结果一致）")

    df = pl.DataFrame({"x": [1, 2, 3, 4, 5]})

    # ① 内置表达式（最佳：向量化 + 并行 + 可优化）。
    native = df.select((pl.col("x") ** 2 + 1).alias("y"))

    # ② map_batches（批处理，内部可 numpy 向量化）。声明输出类型，避免
    # Polars 为类型推断用样例数据额外调用函数。
    batched = df.select(
        pl.col("x")
        .map_batches(
            lambda s: s**2 + 1,
            return_dtype=pl.Int64,
            is_elementwise=True,
        )
        .alias("y")
    )

    # ③ map_elements（逐元素回调 Python，最后手段）。
    # 此写法会触发 PolarsInefficientMapWarning——本节正是要演示这一"降级信号"，
    # 属预期行为，故局部抑制，避免污染教学输出。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pl.exceptions.PolarsInefficientMapWarning)
        element = df.select(
            pl.col("x").map_elements(lambda v: v**2 + 1, return_dtype=pl.Int64).alias("y")
        )

    show("① 内置表达式", native["y"].to_list())
    show("② map_batches", batched["y"].to_list())
    show("③ map_elements", element["y"].to_list())
    show("三者结果一致", native.equals(batched) and batched.equals(element))


def demo_map_batches_numpy() -> None:
    """map_batches + numpy：函数内对批次使用 numpy 向量化。

    用 Polars 没有直接对应的 np.sinc 演示"借 numpy 补能力"。Polars 可能
    按执行策略调用多个批次，因此函数必须纯净，不能依赖调用次数或外部状态。
    """
    section("2) map_batches + numpy 批量向量化")

    df = pl.read_parquet(ORDERS_PARQUET).select("order_id", "quantity")
    result = df.with_columns(
        # Series → numpy → sinc → 交回 Polars。该变换逐元素且长度不变，
        # 因此可以声明 is_elementwise=True 供执行器安全切分批次。
        pl.col("quantity")
        .map_batches(
            lambda s: pl.Series(np.sinc(s.to_numpy())),
            return_dtype=pl.Float64,
            is_elementwise=True,
        )
        .alias("sinc_qty")
    )
    show("sinc(quantity) 前 5 行", result.head(5))


def demo_struct_udf() -> None:
    """struct 传多列给 UDF：把 unit_price 和 quantity 打包传入。

    UDF 需要多个输入列时，用 pl.struct 打包，函数内用 struct.field 取列。
    """
    section("3) struct 传多列给 UDF")

    df = pl.read_parquet(ORDERS_PARQUET).join(
        pl.read_parquet(PRODUCTS_PARQUET), on="product_id"
    )

    def gross_udf(s: pl.Series) -> pl.Series:
        """自定义函数：从 struct Series 计算毛额 = 单价 × 数量。

        参数:
            s: 含 unit_price / quantity 字段的 struct Series。
        返回:
            毛额 Series。
        """
        return s.struct.field("unit_price") * s.struct.field("quantity")

    result = df.select(
        "order_id",
        pl.struct(["unit_price", "quantity"])
        .map_batches(
            gross_udf,
            return_dtype=pl.Float64,
            is_elementwise=True,
        )
        .alias("gross"),
    )
    show("struct UDF 计算毛额", result.head(5))


def demo_map_elements_mitigation() -> None:
    """map_elements 止损：先过滤缩小规模，并显式传 return_dtype。

    假设某逻辑必须逐元素（这里用示意函数），演示"只对需要的子集 map"，
    而非全表调用，把逐元素的代价降到最低。
    """
    section("4) map_elements 止损（先过滤再 map）")

    df = pl.read_parquet(ORDERS_PARQUET).select("order_id", "quantity")

    def label(v: int) -> str:
        """示意的逐元素逻辑：给数量打文字标签。"""
        return "bulk" if v >= 4 else "small"

    # 止损：先过滤出大额订单，只对这个子集逐元素 map（并传 return_dtype）。
    result = (
        df.filter(pl.col("quantity") >= 4)  # 先缩小规模
        .with_columns(
            pl.col("quantity")
            .map_elements(label, return_dtype=pl.String)  # 显式 dtype
            .alias("size_label")
        )
    )
    show("仅对子集 map 的结果", result.head(5))
    show("map 调用作用的行数（已缩小）", result.height)


def demo_numpy_interop() -> None:
    """numpy 互转：Polars → numpy → 计算 → 回 Polars。

    数值列无缺失时 to_numpy 通常零拷贝；把 numpy 结果用 pl.Series 转回。
    """
    section("5) numpy 互转（to_numpy / from_numpy）")

    s = pl.read_parquet(ORDERS_PARQUET)["quantity"]
    arr = s.to_numpy()  # Polars → numpy
    show("to_numpy 类型", type(arr).__name__)
    show("numpy 上算标准差", f"{arr.std():.4f}")

    # numpy 结果转回 Polars。
    back = pl.Series("q_squared", arr**2)
    show("from numpy 转回 Polars（前5）", back.head(5).to_list())


def demo_text_viz() -> None:
    """文本可视化：把渠道销售额分布打印成 ASCII 条形图。

    演示"Polars 算出绘图就绪的小表 → 交给展示层"的衔接思路。
    真实项目里把这段替换成 matplotlib/plot 即可，这里用零依赖的文本条形。
    """
    section("6) 文本可视化（聚合结果 → ASCII 条形）")

    revenue = (
        pl.col("unit_price") * pl.col("quantity") * (1 - pl.col("discount").fill_null(0))
    )
    summary = (
        pl.read_parquet(ORDERS_PARQUET)
        .join(pl.read_parquet(PRODUCTS_PARQUET), on="product_id")
        .with_columns(revenue.alias("revenue"))
        .group_by("channel")
        .agg(pl.col("revenue").sum().alias("total"))
        .sort("total", descending=True)
    )

    # 把聚合结果渲染成 ASCII 条形（最大值对应 40 个字符宽）。
    rows = summary.to_dicts()
    max_total = max(r["total"] for r in rows)
    print()
    for r in rows:
        bar_len = int(r["total"] / max_total * 40)
        print(f"  {r['channel']:<6} | {'█' * bar_len} {r['total']:,.0f}")


def main() -> None:
    """依次运行全部演示。"""
    ensure_data_exists()
    demo_ladder()
    demo_map_batches_numpy()
    demo_struct_udf()
    demo_map_elements_mitigation()
    demo_numpy_interop()
    demo_text_viz()


if __name__ == "__main__":
    main()
