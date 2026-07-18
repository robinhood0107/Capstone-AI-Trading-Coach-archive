"""Property evidence wrapper가 source/profile closure를 build 전에 고정하는지 검사한다."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
HASKELL_ROOT = TOOLS_ROOT.parent
PLAN_PATH = HASKELL_ROOT.parent / "benchmarks/benchmark-plan.v1.json"
EVIDENCE_MODULE = TOOLS_ROOT / "haskell_evidence.py"
SPEC = importlib.util.spec_from_file_location("haskell_evidence", EVIDENCE_MODULE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load haskell_evidence.py")
haskell_evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = haskell_evidence
SPEC.loader.exec_module(haskell_evidence)


def pending_profile() -> dict[str, object]:
    """Baseline pending profile의 최소 exact-object fixture를 만든다."""

    options = ["-O0", "-fasm"]
    return {
        "schemaVersion": "s1.4x-haskell-selected-profile-pending-v1",
        "selectionStatus": "PENDING_BASELINE",
        "profileId": "baseline-o0-fasm",
        "ghcOptions": options,
        "compilerVersion": "9.10.3",
        "compilerSha256": "1" * 64,
        "sourceTreeSha256": "2" * 64,
        "optionsSha256": haskell_evidence.canonical_sha256(options),
        "qualificationPlanSha256": "3" * 64,
        "selectorConfigSha256": "4" * 64,
        "fallbackProfile": "baseline-o0-fasm",
        "selectedBy": "pending-fail-closed-baseline",
        "fullCorrectnessStatus": "NOT_RUN",
        "qualificationStatus": "NOT_RUN",
    }


class PropertyWrapperContractTests(unittest.TestCase):
    def _candidate_checkout(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        files = {
            "src/Risk.hs": "module Risk (value) where\nvalue = 1\n",
            "app/Main.hs": "module Main (main) where\nmain = pure ()\n",
            "test/TestMain.hs": "module TestMain (main) where\nmain = pure ()\n",
            "benchmark/BenchMain.hs": "module BenchMain (main) where\nmain = pure ()\n",
            "package.yaml": "name: sample\n",
            "selected-profile.v1.json": "{}\n",
        }
        for relative, content in files.items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", *files], check=True)
        return temporary, root

    def test_pending_profile_requires_exact_o0_fasm_identity(self) -> None:
        profile = pending_profile()
        validated = haskell_evidence.validate_selected_profile_document(
            profile,
            expected_compiler_sha256="1" * 64,
            expected_source_tree_sha256="2" * 64,
            expected_qualification_plan_sha256="3" * 64,
            expected_selector_config_sha256="4" * 64,
        )
        self.assertEqual(validated["ghcOptions"], ["-O0", "-fasm"])

        for options in (["-O2", "-fasm"], ["-O0"], ["-fasm", "-O0"]):
            with self.subTest(options=options):
                altered = {**profile, "ghcOptions": options}
                altered["optionsSha256"] = haskell_evidence.canonical_sha256(options)
                with self.assertRaisesRegex(
                    haskell_evidence.EvidenceError,
                    "pending baseline profile options",
                ):
                    haskell_evidence.validate_selected_profile_document(
                        altered,
                        expected_compiler_sha256="1" * 64,
                        expected_source_tree_sha256="2" * 64,
                        expected_qualification_plan_sha256="3" * 64,
                        expected_selector_config_sha256="4" * 64,
                    )

    def test_tracked_pending_profile_is_current_canonical_baseline(self) -> None:
        profile_path = HASKELL_ROOT / "selected-profile.v1.json"
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(HASKELL_ROOT),
                "ls-files",
                "--error-unmatch",
                "--",
                profile_path.name,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        plan = haskell_evidence.strict_json_load(PLAN_PATH)
        profile = haskell_evidence.strict_json_load(profile_path)
        validated = haskell_evidence.validate_selected_profile_document(
            profile,
            expected_compiler_sha256=haskell_evidence.AUTHORITATIVE_GHC_SHA256,
            expected_source_tree_sha256=(
                haskell_evidence.benchmark_source_tree_sha256(HASKELL_ROOT)
            ),
            expected_qualification_plan_sha256=haskell_evidence.sha256_file(
                PLAN_PATH
            ),
            expected_selector_config_sha256=haskell_evidence.canonical_sha256(
                plan["haskellProfileQualification"]
            ),
        )

        self.assertEqual(
            validated["schemaVersion"],
            "s1.4x-haskell-selected-profile-pending-v1",
        )
        self.assertEqual(validated["selectionStatus"], "PENDING_BASELINE")
        self.assertEqual(
            profile_path.read_bytes(),
            haskell_evidence.canonical_json_bytes(
                validated,
                trailing_newline=True,
            ),
        )

    def test_tracked_source_manifest_is_the_exact_current_candidate_set(self) -> None:
        manifest_path = HASKELL_ROOT / "source-inputs.v1.json"
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(HASKELL_ROOT),
                "ls-files",
                "--error-unmatch",
                "--",
                manifest_path.name,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        manifest = haskell_evidence.validate_source_manifest(
            HASKELL_ROOT,
            manifest_path,
        )
        expected_paths = {
            path.relative_to(HASKELL_ROOT).as_posix()
            for root_name in haskell_evidence.CANDIDATE_ROOTS
            for path in (HASKELL_ROOT / root_name).rglob("*.hs")
        }
        expected_paths.update({"package.yaml", "selected-profile.v1.json"})

        self.assertEqual(set(manifest["files"]), expected_paths)
        self.assertEqual(
            manifest_path.read_bytes(),
            haskell_evidence.canonical_json_bytes(
                manifest,
                trailing_newline=True,
            ),
        )

    def test_profile_unknown_field_is_rejected(self) -> None:
        altered = {**pending_profile(), "unknown": True}

        with self.assertRaisesRegex(
            haskell_evidence.EvidenceError,
            "selected profile field set",
        ):
            haskell_evidence.validate_selected_profile_document(
                altered,
                expected_compiler_sha256="1" * 64,
                expected_source_tree_sha256="2" * 64,
                expected_qualification_plan_sha256="3" * 64,
                expected_selector_config_sha256="4" * 64,
            )

    def test_manifest_and_profile_validation_precede_stack_build(self) -> None:
        wrapper = (TOOLS_ROOT / "run-property-evidence.sh").read_text(
            encoding="utf-8"
        )
        build = wrapper.index('"$STACK_BIN" "${STACK_ARGUMENTS[@]}" build')
        manifest = wrapper.index('"$HASKELL_ROOT/tools/haskell_evidence.py" source-inputs')
        profile = wrapper.index('"$HASKELL_ROOT/tools/haskell_evidence.py" selected-profile')

        self.assertLess(manifest, build)
        self.assertLess(profile, build)

    def test_wrapper_rejects_ambient_stack_configuration(self) -> None:
        wrapper = (TOOLS_ROOT / "run-property-evidence.sh").read_text(
            encoding="utf-8"
        )
        for variable in ("STACK_YAML", "STACK_ROOT", "STACK_OPTS", "STACK_CONFIG"):
            with self.subTest(variable=variable):
                self.assertIn(f"${{{variable}+x}}", wrapper)

    def test_wrapper_binds_stack_root_to_the_explicit_cache_root(self) -> None:
        wrapper = (TOOLS_ROOT / "run-property-evidence.sh").read_text(
            encoding="utf-8"
        )
        for token in (
            "S1_4X_CACHE_ROOT",
            "isolated-stack-root",
            "--purpose property",
            "--output \"$OUTPUT_DIRECTORY\"",
            "--stack-root",
            "STACK_ROOT_PATH",
        ):
            with self.subTest(token=token):
                self.assertIn(token, wrapper)
        self.assertNotIn('STACK_ROOT_PATH="${CACHE_ROOT}/stack-root"', wrapper)
        self.assertIn("--hpack-force", wrapper)

    def test_wrapper_forces_and_hashes_selected_profile_options(self) -> None:
        wrapper = (TOOLS_ROOT / "run-property-evidence.sh").read_text(
            encoding="utf-8"
        )
        for token in (
            "--ghc-options",
            "PROFILE_GHC_OPTIONS",
            "PROFILE_OPTIONS_SHA256",
            "BUILD_ARGV_SHA256",
            "SELECTED_PROFILE_SHA256",
        ):
            with self.subTest(token=token):
                self.assertIn(token, wrapper)

    def test_wrapper_revalidates_the_pre_build_closure_after_every_execution_phase(
        self,
    ) -> None:
        wrapper = (TOOLS_ROOT / "run-property-evidence.sh").read_text(
            encoding="utf-8"
        )
        for phase in ("pre-build", "post-build", "post-run"):
            with self.subTest(phase=phase):
                self.assertIn(f'validate_execution_closure "{phase}"', wrapper)
        for token in (
            "EXPECTED_SOURCE_MANIFEST_SHA256",
            "EXPECTED_SOURCE_TREE_SHA256",
            "EXPECTED_PROPERTY_CLOSURE_SHA256",
        ):
            with self.subTest(token=token):
                self.assertIn(token, wrapper)

    def test_haskell_closure_requires_manifest_and_selected_profile(self) -> None:
        source = (
            HASKELL_ROOT / "test/S14X/PropertyEvidence.hs"
        ).read_text(encoding="utf-8")
        self.assertIn('root </> "source-inputs.v1.json"', source)
        self.assertIn('root </> "selected-profile.v1.json"', source)
        self.assertNotIn("filterMFile", source)

    def test_haskell_rechecks_expected_manifest_profile_and_source_tree_hashes(
        self,
    ) -> None:
        source = (
            HASKELL_ROOT / "test/S14X/PropertyEvidence.hs"
        ).read_text(encoding="utf-8")
        for token in (
            "expectedSourceManifestHash",
            "expectedSourceTreeHash",
            "expectedPropertyClosureHash",
            "validateExecutionClosure",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_missing_selected_profile_fails_the_source_closure(self) -> None:
        temporary, root = self._candidate_checkout()
        with temporary:
            (root / "selected-profile.v1.json").unlink()
            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "required configuration input is missing",
            ):
                haskell_evidence.build_source_manifest(root)

    def test_missing_or_stale_manifest_fails_validation(self) -> None:
        temporary, root = self._candidate_checkout()
        with temporary:
            manifest_path = root / "source-inputs.v1.json"
            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "invalid JSON input",
            ):
                haskell_evidence.validate_source_manifest(root, manifest_path)

            haskell_evidence.atomic_write_json(
                manifest_path,
                haskell_evidence.build_source_manifest(root),
            )
            (root / "src/Risk.hs").write_text(
                "module Risk (value) where\nvalue = 2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "source-input manifest drift",
            ):
                haskell_evidence.validate_source_manifest(root, manifest_path)

    def test_untracked_extra_source_fails_the_source_closure(self) -> None:
        temporary, root = self._candidate_checkout()
        with temporary:
            (root / "src/Extra.hs").write_text(
                "module Extra (value) where\nvalue = 2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "untracked candidate input",
            ):
                haskell_evidence.build_source_manifest(root)

    def test_tracked_non_source_under_compile_root_fails_the_exact_set(self) -> None:
        temporary, root = self._candidate_checkout()
        with temporary:
            extra = root / "src/README.txt"
            extra.write_text("not a compiler input\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "src/README.txt"],
                check=True,
            )
            with self.assertRaisesRegex(
                haskell_evidence.EvidenceError,
                "tracked source input set mismatch",
            ):
                haskell_evidence.build_source_manifest(root)


if __name__ == "__main__":
    unittest.main()
