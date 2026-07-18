"""Python/NumPy/JAX benchmark boundary의 portable evidence 회귀 테스트."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

import numpy as np

INTEGRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION))

from python_benchmark_block import _consume, _source_closure_sha256  # noqa: E402


@dataclass(frozen=True, slots=True)
class NestedResult:
    statistic: float
    values: tuple[np.ndarray, float]


class PythonBenchmarkEvidenceTests(TestCase):
    def test_slotted_dataclass_and_arrays_are_fully_consumed(self) -> None:
        result = NestedResult(
            statistic=2.0,
            values=(np.asarray([1.0, 3.0], dtype=np.float64), 4.0),
        )
        self.assertEqual(_consume(result), 10.0)

    def test_source_closure_hash_is_independent_of_checkout_absolute_path(self) -> None:
        first = Path(self.enterContext(tempfile.TemporaryDirectory()))
        second = Path(self.enterContext(tempfile.TemporaryDirectory()))
        relative_paths = (Path("src/core.py"), Path("src/model.py"))
        for root in (first, second):
            for relative in relative_paths:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"source:{relative.as_posix()}\n", encoding="utf-8")
        first_digest = _source_closure_sha256(
            first,
            [first / relative for relative in relative_paths],
        )
        second_digest = _source_closure_sha256(
            second,
            [second / relative for relative in relative_paths],
        )
        self.assertEqual(first_digest, second_digest)
