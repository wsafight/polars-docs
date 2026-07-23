"""
共享工具模块：被 code/ 下所有章节脚本复用。

设计目标：
- 统一数据目录 / 数据集文件路径，避免各脚本各写一份路径逻辑。
- 提供带标题的打印辅助，让每个示例的输出在终端里段落分明、便于对照阅读。

约定：数据集由 `code/00_generate_data.py` 一次性生成到项目根的 `data/` 目录，
其余章节脚本只读取、不重新生成，保证跨章节使用同一份"确定性"数据。
"""

from __future__ import annotations

from pathlib import Path

# 项目根目录：本文件位于 <root>/code/_common.py，因此上溯两级即为根。
ROOT = Path(__file__).resolve().parent.parent
# 所有 CSV / Parquet 数据集统一存放目录。
DATA_DIR = ROOT / "data"

# 三张核心数据表的标准路径（事实表 orders + 维度表 customers / products）。
ORDERS_CSV = DATA_DIR / "orders.csv"
ORDERS_PARQUET = DATA_DIR / "orders.parquet"
CUSTOMERS_CSV = DATA_DIR / "customers.csv"
CUSTOMERS_PARQUET = DATA_DIR / "customers.parquet"
PRODUCTS_CSV = DATA_DIR / "products.csv"
PRODUCTS_PARQUET = DATA_DIR / "products.parquet"


def section(title: str) -> None:
    """打印一个醒目的分节标题。

    用于在单个脚本包含多个演示片段时，将终端输出切分成清晰的段落，
    方便读者把"代码块"与"输出"一一对应。

    参数:
        title: 分节标题文本。
    """
    line = "=" * 70
    print(f"\n{line}\n▶ {title}\n{line}")


def show(label: str, obj: object) -> None:
    """打印一个带标签的对象。

    统一"标签 + 换行 + 内容"的输出格式，避免每处调用都手写 print 排版。

    参数:
        label: 该输出的说明性标签（例如 "结果" / "schema"）。
        obj:   要打印的任意对象（DataFrame、Series、Python 值等）。
    """
    print(f"\n【{label}】")
    print(obj)


def ensure_data_exists() -> None:
    """确保数据集已生成，否则给出明确的引导提示并终止。

    所有非生成类章节脚本在开头调用它，做一次"前置条件检查"：
    如果用户还没跑过数据生成脚本，就不要抛出令人困惑的 FileNotFoundError，
    而是直接告诉他该运行哪条命令。
    """
    if not ORDERS_PARQUET.exists():
        raise SystemExit(
            "未找到数据集，请先运行：\n    uv run code/00_generate_data.py"
        )
