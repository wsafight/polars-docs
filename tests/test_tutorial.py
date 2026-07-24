from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "code"
sys.path.insert(0, str(CODE_DIR))

TUTORIAL_SCRIPTS = tuple(
    path
    for path in sorted(CODE_DIR.glob("[0-9][0-9]_*.py"))
    if path.name != "00_generate_data.py"
)


def load_chapter(filename: str):
    """Load a numbered tutorial script as a module."""
    path = CODE_DIR / filename
    module_name = f"tutorial_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_dataset_invariants() -> None:
    chapter = load_chapter("00_generate_data.py")
    rng = np.random.default_rng(chapter.SEED)

    customers = chapter.make_customers(rng)
    products = chapter.make_products(rng)
    orders = chapter.make_orders(rng)

    assert customers.shape == (200, 5)
    assert products.shape == (40, 4)
    assert orders.shape == (5020, 8)
    assert orders.height - orders.unique().height == 20
    assert orders["discount"].null_count() > 0


@pytest.mark.parametrize("script", TUTORIAL_SCRIPTS, ids=lambda path: path.stem)
def test_tutorial_script_runs_from_the_command_line(script: Path) -> None:
    """Exercise every chapter through the same entry point readers use."""
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, (
        f"{script.name} failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_intro_engines_produce_the_same_result() -> None:
    chapter = load_chapter("00_intro.py")

    pandas_result = pl.from_pandas(chapter.with_pandas())
    eager_result = chapter.with_polars_eager()
    lazy_result = chapter.with_polars_lazy()
    duckdb_result = chapter.with_duckdb()

    assert_frame_equal(eager_result, lazy_result, check_row_order=False)
    assert_frame_equal(eager_result, duckdb_result, check_row_order=False)
    assert_frame_equal(eager_result, pandas_result, check_row_order=False)


def test_lazy_plan_keeps_projection_and_predicate_pushdown() -> None:
    chapter = load_chapter("04_lazy_optimizer.py")
    plan = chapter.build_query().explain(optimized=True)

    assert "PROJECT 2/8" in plan
    assert "SELECTION" in plan


def test_end_to_end_branches_stay_lazy_and_collect_together() -> None:
    chapter = load_chapter("14_end_to_end.py")
    clean = chapter.build_clean_orders()
    plans = [
        chapter.analysis_monthly_channel(clean),
        chapter.analysis_city_top_category(clean),
        chapter.analysis_high_value_customers(clean),
    ]

    assert all(isinstance(plan, pl.LazyFrame) for plan in plans)
    monthly, city_top, high_value = pl.collect_all(plans)

    assert monthly.height > 0
    assert city_top.height >= city_top["city"].n_unique()
    assert city_top["rank_in_city"].eq(1).all()
    assert high_value["total_spent"].gt(2500).all()
