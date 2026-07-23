"""
00 · 生成可复现数据集
=====================================================================
本脚本用固定随机种子生成一套"电商订单"数据，供全部章节复用。
之所以自己造数据而不下载真实数据集，是为了：
  1) 确定性：固定 seed，任何人任何时候跑出的数据完全一致，便于对照输出。
  2) 无网络依赖：离线即可完整学习。
  3) 刻意留坑：掺入 null、重复行、脏字符串、时间戳，用来演示后续清洗/重塑。

数据模型（经典的星型结构：1 张事实表 + 2 张维度表）：
  - customers 客户维度：customer_id, name, city, signup_date, tier
  - products  商品维度：product_id, product_name, category, unit_price
  - orders    订单事实：order_id, customer_id, product_id, quantity,
                         order_ts, discount, channel, note

运行：
    uv run code/00_generate_data.py
"""

from __future__ import annotations

import numpy as np
import polars as pl

from _common import (
    CUSTOMERS_CSV,
    CUSTOMERS_PARQUET,
    DATA_DIR,
    ORDERS_CSV,
    ORDERS_PARQUET,
    PRODUCTS_CSV,
    PRODUCTS_PARQUET,
    section,
    show,
)

# 全局随机种子：所有随机过程都从它派生，确保整套数据可复现。
SEED = 42
N_CUSTOMERS = 200
N_PRODUCTS = 40
N_ORDERS = 5000


def make_customers(rng: np.random.Generator) -> pl.DataFrame:
    """生成客户维度表。

    刻意在 city 列注入少量 null，用于后续演示缺失值处理；
    tier（会员等级）为有序类别，后续可演示 Categorical / 排序聚合。

    参数:
        rng: numpy 随机数生成器（已绑定种子）。
    返回:
        customers 维度表 DataFrame。
    """
    cities = ["Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Chengdu", None]
    tiers = ["bronze", "silver", "gold", "platinum"]

    customer_ids = np.arange(1, N_CUSTOMERS + 1)
    # signup_date：以 2023-01-01 为基准，加上 0~364 天的随机偏移。
    # 直接用 numpy 的 datetime64 计算，避免把 Polars 表达式塞进构造器。
    base = np.datetime64("2023-01-01")
    signup_date = base + rng.integers(0, 365, size=N_CUSTOMERS).astype("timedelta64[D]")

    return pl.DataFrame(
        {
            "customer_id": customer_ids,
            "name": [f"user_{i:04d}" for i in customer_ids],
            # 用带 None 的候选池随机取值，天然引入缺失城市。
            "city": rng.choice(cities, size=N_CUSTOMERS, p=[0.28, 0.24, 0.2, 0.12, 0.1, 0.06]),
            "signup_date": signup_date,
            "tier": rng.choice(tiers, size=N_CUSTOMERS, p=[0.4, 0.3, 0.2, 0.1]),
        }
    )


def make_products(rng: np.random.Generator) -> pl.DataFrame:
    """生成商品维度表。

    unit_price 用对数正态分布模拟"多数便宜、少数昂贵"的长尾价格分布。

    参数:
        rng: numpy 随机数生成器。
    返回:
        products 维度表 DataFrame。
    """
    categories = ["electronics", "books", "home", "toys", "grocery"]
    product_ids = np.arange(1, N_PRODUCTS + 1)
    prices = np.round(rng.lognormal(mean=3.2, sigma=0.6, size=N_PRODUCTS), 2)

    return pl.DataFrame(
        {
            "product_id": product_ids,
            "product_name": [f"prod_{i:03d}" for i in product_ids],
            "category": rng.choice(categories, size=N_PRODUCTS),
            "unit_price": prices,
        }
    )


def make_orders(rng: np.random.Generator) -> pl.DataFrame:
    """生成订单事实表，并刻意注入"脏数据"用于后续清洗演示。

    注入的坑：
      - discount 列含 null（约 15% 缺失）。
      - note 列含前后空格 / 大小写混乱的脏字符串。
      - 追加若干条完全重复行，用于演示去重。
      - order_ts 为精确到秒的时间戳，用于时间序列章节。

    参数:
        rng: numpy 随机数生成器。
    返回:
        orders 事实表 DataFrame（含重复行）。
    """
    order_ids = np.arange(1, N_ORDERS + 1)
    channels = ["web", "app", "store"]
    raw_notes = [" OK ", "urgent", "Gift", "gift ", "  ", "VIP", "return?"]

    # order_ts：在 90 天窗口内、按秒随机分布的下单时间。
    # 注意：Polars 仅支持 ms/us/ns 分辨率的 datetime64，因此按秒算好后转成 us。
    start = np.datetime64("2024-01-01T00:00:00", "us")
    offsets = rng.integers(0, 90 * 24 * 3600, size=N_ORDERS)
    order_ts = start + offsets.astype("timedelta64[s]").astype("timedelta64[us]")

    # discount：0~0.3 的折扣，其中约 15% 置为 null 模拟缺失。
    # 用 Python list（含 None）交给 Polars 推断，比 numpy object 数组更稳妥。
    discount_vals = np.round(rng.uniform(0, 0.3, size=N_ORDERS), 2)
    mask_null = rng.random(N_ORDERS) < 0.15
    discount = [None if m else float(v) for v, m in zip(discount_vals, mask_null)]

    df = pl.DataFrame(
        {
            "order_id": order_ids,
            "customer_id": rng.integers(1, N_CUSTOMERS + 1, size=N_ORDERS),
            "product_id": rng.integers(1, N_PRODUCTS + 1, size=N_ORDERS),
            "quantity": rng.integers(1, 6, size=N_ORDERS),
            "order_ts": order_ts,
            "discount": discount,
            "channel": rng.choice(channels, size=N_ORDERS, p=[0.5, 0.35, 0.15]),
            "note": rng.choice(raw_notes, size=N_ORDERS),
        },
    )

    # 追加 20 条重复行（复制前 20 行），用于第 13 章演示 unique() 去重。
    duplicates = df.head(20)
    df = pl.concat([df, duplicates], how="vertical")
    return df


def main() -> None:
    """脚本入口：生成三张表并同时落盘为 CSV 与 Parquet 两种格式。

    同时产出两种格式的原因：
      - CSV：人类可读、演示"脏数据/类型推断"更直观。
      - Parquet：列式 + 带 schema，演示 scan/惰性/性能时更贴近生产。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 用同一个种子派生生成器，保证每次运行结果完全一致。
    rng = np.random.default_rng(SEED)

    customers = make_customers(rng)
    products = make_products(rng)
    orders = make_orders(rng)

    # 分别写出 CSV 与 Parquet。
    # 始终引用字段，既保留 note 中刻意注入的首尾空格，也避免 CSV 文件本身
    # 出现会让 git diff --check 失败的行尾空白。
    customers.write_csv(CUSTOMERS_CSV, quote_style="always")
    customers.write_parquet(CUSTOMERS_PARQUET)
    products.write_csv(PRODUCTS_CSV, quote_style="always")
    products.write_parquet(PRODUCTS_PARQUET)
    orders.write_csv(ORDERS_CSV, quote_style="always")
    orders.write_parquet(ORDERS_PARQUET)

    section("数据集生成完成")
    show("customers 预览", customers.head(5))
    show("products 预览", products.head(5))
    show("orders 预览", orders.head(5))
    print(
        f"\n已写入 {DATA_DIR}："
        f"\n  customers: {customers.height} 行"
        f"\n  products : {products.height} 行"
        f"\n  orders   : {orders.height} 行（含 20 条重复）"
    )


if __name__ == "__main__":
    main()
