"""여섯 boundary가 같은 frozen fixture/case/DSR 입력을 증명하는지 검증한다."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
REPO = S1_4X.parents[3]
BENCHMARKS = S1_4X / "benchmarks"
LARGE = S1_4X / "contract/fixtures/large"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(BENCHMARKS))

from benchmark_input_ledger import (  # noqa: E402
    build_input_ledger,
    validate_input_ledger,
)
from gate import GateError, strict_json_load  # noqa: E402
from materialize_large_fixtures import materialize  # noqa: E402
from python_benchmark_block import _generated_array  # noqa: E402

PLAN = BENCHMARKS / "benchmark-plan.v1.json"


class BenchmarkInputLedgerTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._materialized_temporary = tempfile.TemporaryDirectory()
        parent = Path(cls._materialized_temporary.name).resolve(strict=True)
        cls.materialized_root = parent / "large-fixture-root"
        cls.materialized_receipt = parent / "large-fixture-receipt.json"
        materialize(
            s1_4x_root=S1_4X,
            output_root=cls.materialized_root,
            receipt=cls.materialized_receipt,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._materialized_temporary.cleanup()

    def test_dsr_case_binds_extreme_mix_and_exact_trial_provenance(self) -> None:
        plan = strict_json_load(PLAN)
        ledger = build_input_ledger(
            plan=plan,
            plan_path=PLAN,
            repo_root=REPO,
            large_fixture_root=self.materialized_root,
            boundary_id="scala",
            selector_id="scala/probabilistic-scalar",
        )
        dsr = next(
            case
            for case in ledger["cases"]
            if case["functionId"] == "deflated_sharpe_ratio"
        )
        self.assertEqual(
            dsr["dsrProvenance"],
            {
                "samplingFrequency": "daily",
                "trialRegistrySha256": "d" * 64,
                "varianceDof": 1,
                "groups": [
                    {
                        "trialCount": 2,
                        "evaluationCount": 5462,
                        "rawTrialCount": 2,
                        "effectiveTrialCount": 2,
                    },
                    {
                        "trialCount": 10**20,
                        "evaluationCount": 5461,
                        "rawTrialCount": 10**20,
                        "effectiveTrialCount": 10**20,
                    },
                    {
                        "trialCount": 10**308,
                        "evaluationCount": 5461,
                        "rawTrialCount": 10**308,
                        "effectiveTrialCount": 10**308,
                    },
                ],
            },
        )
        self.assertEqual(dsr["inputSlices"][0]["shape"], [16384])
        self.assertEqual(dsr["inputSlices"][0]["offsetElements"], 0)

        validate_input_ledger(
            ledger,
            plan=plan,
            plan_path=PLAN,
            repo_root=REPO,
            large_fixture_root=self.materialized_root,
            boundary_id="scala",
            selector_id="scala/probabilistic-scalar",
        )
        modified = copy.deepcopy(ledger)
        modified["cases"][1]["functionArguments"]["sample_size"] = 251
        with self.assertRaisesRegex(GateError, "BENCHMARK_INPUT_LEDGER_MISMATCH"):
            validate_input_ledger(
                modified,
                plan=plan,
                plan_path=PLAN,
                repo_root=REPO,
                large_fixture_root=self.materialized_root,
                boundary_id="scala",
                selector_id="scala/probabilistic-scalar",
            )

    def test_ledger_uses_only_portable_materialized_root_identity(self) -> None:
        plan = strict_json_load(PLAN)
        ledger = build_input_ledger(
            plan=plan,
            plan_path=PLAN,
            repo_root=REPO,
            large_fixture_root=self.materialized_root,
            boundary_id="haskell",
            selector_id="haskell/coverage-batch",
        )

        serialized = json.dumps(ledger, allow_nan=False, sort_keys=True)
        self.assertEqual(
            ledger["materializedRootPathId"],
            "S1_4X_LARGE_FIXTURE_ROOT",
        )
        self.assertNotIn(str(self.materialized_root), serialized)
        self.assertTrue(
            all(
                fixture["manifestPath"].startswith("S1_4X_LARGE_FIXTURE_ROOT/large/")
                and fixture["payloadPath"].startswith(
                    "S1_4X_LARGE_FIXTURE_ROOT/large/generated/"
                )
                for fixture in ledger["fixtures"]
            )
        )

    def test_ledger_rejects_tracked_generated_fallback_and_wrong_large_root(
        self,
    ) -> None:
        plan = strict_json_load(PLAN)
        arguments = {
            "plan": plan,
            "plan_path": PLAN,
            "repo_root": REPO,
            "boundary_id": "scala",
            "selector_id": "scala/path-transform",
        }
        with self.assertRaisesRegex(
            GateError,
            "BENCHMARK_LARGE_FIXTURE_ROOT_INVALID",
        ):
            build_input_ledger(
                **arguments,
                large_fixture_root=S1_4X / "contract/fixtures",
            )
        with self.assertRaisesRegex(
            GateError,
            "BENCHMARK_LARGE_FIXTURE_ROOT_INVALID",
        ):
            build_input_ledger(
                **arguments,
                large_fixture_root=LARGE,
            )

        payload = self.materialized_root / "large/generated/large-prices-n100000.f64le"
        original = payload.read_bytes()
        try:
            payload.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            with self.assertRaisesRegex(
                GateError,
                "GENERATED_FIXTURE_DIGEST_MISMATCH",
            ):
                build_input_ledger(
                    **arguments,
                    large_fixture_root=self.materialized_root,
                )
        finally:
            payload.write_bytes(original)

    def test_generated_array_rejects_payload_digest_drift_and_symlink(self) -> None:
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        copied_s1 = temporary / "s1-4x-numeric-parity"
        copied_large = copied_s1 / "contract/fixtures/large"
        generated = copied_large / "generated"
        generated.mkdir(parents=True)
        (copied_s1 / "oracle").mkdir()
        shutil.copyfile(
            S1_4X / "contract/contract-manifest.v1.json",
            copied_s1 / "contract/contract-manifest.v1.json",
        )
        shutil.copyfile(
            S1_4X / "oracle/generate_large_fixtures.py",
            copied_s1 / "oracle/generate_large_fixtures.py",
        )
        manifest_name = "large-prices-n100000.manifest.json"
        binary_name = "large-prices-n100000.f64le"
        shutil.copyfile(LARGE / manifest_name, copied_large / manifest_name)
        materialized_large = self.materialized_root / "large"
        shutil.copyfile(
            materialized_large / "generated" / binary_name,
            generated / binary_name,
        )

        values = _generated_array(copied_large, binary_name, 100000)
        self.assertEqual(values.shape, (100000,))

        binary = generated / binary_name
        original = binary.read_bytes()
        binary.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaisesRegex(GateError, "GENERATED_FIXTURE_DIGEST_MISMATCH"):
            _generated_array(copied_large, binary_name, 100000)

        binary.unlink()
        binary.symlink_to(materialized_large / "generated" / binary_name)
        with self.assertRaisesRegex(GateError, "GENERATED_FIXTURE_UNSAFE"):
            _generated_array(copied_large, binary_name, 100000)
