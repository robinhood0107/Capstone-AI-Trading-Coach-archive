"""stylish-haskell GHC2024 parser fallback의 fail-closed 계약 회귀 테스트."""

from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
MODULE_PATH = TOOLS_ROOT / "stylish_fallback.py"
CONTRACT_PATH = HASKELL_ROOT / "stylish-ghc2024-fallback.v1.json"
MANDATED_CONFIG_PATH = HASKELL_ROOT / ".stylish-haskell.yaml"
DERIVED_CONFIG_PATH = HASKELL_ROOT / ".stylish-haskell-ghc2024-expanded.yaml"
BENCHMARK_PATH = HASKELL_ROOT / "benchmark" / "Main.hs"

SPEC = importlib.util.spec_from_file_location("stylish_fallback", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load stylish_fallback.py")
stylish_fallback = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stylish_fallback
SPEC.loader.exec_module(stylish_fallback)


class StylishFallbackTests(unittest.TestCase):
    def load_contract(self) -> dict[str, object]:
        return stylish_fallback.strict_json_load(CONTRACT_PATH)

    def validate(
        self,
        contract: dict[str, object],
        *,
        mandated: bytes | None = None,
        derived: bytes | None = None,
        package: bytes | None = None,
    ) -> object:
        return stylish_fallback.validate_fallback_contract(
            contract,
            mandated_configuration=(
                MANDATED_CONFIG_PATH.read_bytes() if mandated is None else mandated
            ),
            derived_configuration=(
                DERIVED_CONFIG_PATH.read_bytes() if derived is None else derived
            ),
            package_configuration=(
                (HASKELL_ROOT / "package.yaml").read_bytes()
                if package is None
                else package
            ),
        )

    def test_exact_ghc_9_10_3_edition_expansion_is_frozen(self) -> None:
        contract = self.load_contract()
        validated = self.validate(contract)

        self.assertEqual(validated.official_extension_count, 54)
        self.assertEqual(validated.effective_extension_count, 52)
        self.assertEqual(
            validated.official_extensions_sha256,
            "3822e8f4c0597c4bb84f628f08e00617e8dac8da1f0eb532991355402e3537cd",
        )
        self.assertEqual(
            validated.effective_extensions_sha256,
            "a13ee7bdfe5bb58a13c69fa1faba5788d1d4eeea0fc5f0fd04c5519a42955033",
        )
        self.assertEqual(
            contract["officialSource"]["uri"],
            "https://downloads.haskell.org/~ghc/9.10.3/docs/users_guide/exts/control.html",
        )
        self.assertEqual(
            contract["officialSource"]["contentSha256"],
            "1abd26d27eb68a9aeca6aeae99b5c232e7d9cfe4339e0409d3f6465c035c8d13",
        )
        self.assertEqual(
            contract["knownCapabilityFailure"]["stderrSha256"],
            "86f713a060c046d33c4067b0e78bb31bdfd43f5dded027038c522d45fbf3d643",
        )

    def test_edition_list_rejects_omission_addition_and_reordering(self) -> None:
        contract = self.load_contract()
        for label, mutate in (
            (
                "omission",
                lambda values: values.pop(0),
            ),
            (
                "addition",
                lambda values: values.append("OverloadedStrings"),
            ),
            (
                "reordering",
                lambda values: values.__setitem__(
                    slice(0, 2),
                    list(reversed(values[0:2])),
                ),
            ),
        ):
            with self.subTest(label=label):
                drifted = copy.deepcopy(contract)
                extensions = drifted["officialSource"]["ghc2024Extensions"]
                mutate(extensions)
                with self.assertRaisesRegex(
                    stylish_fallback.FallbackError,
                    "official GHC2024 extension list drift",
                ):
                    self.validate(drifted)

    def test_project_no_overrides_are_exact_and_separate(self) -> None:
        contract = self.load_contract()
        drifted = copy.deepcopy(contract)
        drifted["projectExtensionOverrides"]["explicitNoExtensions"].pop()

        with self.assertRaisesRegex(
            stylish_fallback.FallbackError,
            "project explicit No extension drift",
        ):
            self.validate(drifted)

    def test_derived_configuration_rejects_omission_and_addition(self) -> None:
        contract = self.load_contract()
        derived = DERIVED_CONFIG_PATH.read_bytes()
        mutations = {
            "omission": derived.replace(b"  - BangPatterns\n", b"", 1),
            "addition": derived + b"  - OverloadedStrings\n",
        }
        for label, drifted in mutations.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    stylish_fallback.FallbackError,
                    "derived formatter configuration drift",
                ):
                    self.validate(contract, derived=drifted)

    def test_mandated_configuration_cannot_be_rewritten_for_the_fallback(self) -> None:
        contract = self.load_contract()
        mandated = MANDATED_CONFIG_PATH.read_bytes().replace(
            b"  - GHC2024\n",
            b"  - GHC2021\n",
            1,
        )

        with self.assertRaisesRegex(
            stylish_fallback.FallbackError,
            "mandated formatter configuration drift",
        ):
            self.validate(contract, mandated=mandated)

    def test_known_capability_failure_leaf_is_not_self_attesting(self) -> None:
        contract = self.load_contract()
        drifted = copy.deepcopy(contract)
        drifted["knownCapabilityFailure"]["stderrSha256"] = "0" * 64

        with self.assertRaisesRegex(
            stylish_fallback.FallbackError,
            "known formatter failure leaf drift",
        ):
            self.validate(drifted)

    def test_format_gate_reproduces_capability_leaf_before_real_source_check(self) -> None:
        script = (TOOLS_ROOT / "check-format.sh").read_text(encoding="utf-8")
        probe = (
            '"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" '
            '"$HASKELL_ROOT/tools/stylish_fallback.py" probe'
        )
        source_inputs = (
            '"$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" '
            '"$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs'
        )

        self.assertLess(script.index(probe), script.index(source_inputs))
        self.assertIn(
            'CONFIGURATION="$HASKELL_ROOT/.stylish-haskell-ghc2024-expanded.yaml"',
            script,
        )
        self.assertIn(
            '"--config=$CONFIGURATION"',
            script,
        )
        self.assertNotIn(
            '"--config=$MANDATED_CONFIGURATION"',
            script,
        )

    def test_negative_gate_asserts_only_frozen_formatter_output(self) -> None:
        contract = self.load_contract()
        script = (TOOLS_ROOT / "check-format.sh").read_text(encoding="utf-8")

        self.assertEqual(
            contract["derivedCapabilityProbe"]["stdoutSha256"],
            "6aeb47fa182fcae71756433963017d80d7b649e00e879f5e8af2c6ca53f8b5ba",
        )
        for emitted_token in (
            "import           Data.Maybe (maybe)",
            "value::Maybe Int->Int",
            "value=maybe 0 id",
        ):
            with self.subTest(emitted_token=emitted_token):
                self.assertIn(emitted_token, script)
        self.assertNotIn('"value :: Maybe Int -> Int"', script)
        self.assertNotIn('"value = maybe 0 id"', script)

    def test_benchmark_aeson_import_matches_frozen_formatter_layout(self) -> None:
        source = BENCHMARK_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "import           Data.Aeson (FromJSON (parseJSON), Value, "
            "eitherDecodeFileStrict', encode, object,\n"
            "                             withObject, (.:), (.=))",
            source,
        )


if __name__ == "__main__":
    unittest.main()
