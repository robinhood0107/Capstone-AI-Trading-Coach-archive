"""Frozen binary manifest catalog 전체 replay의 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

INTEGRATION = Path(__file__).resolve().parents[1]
S1_4X = INTEGRATION.parent
INVALID = S1_4X / "contract" / "fixtures" / "invalid"
sys.path.insert(0, str(INTEGRATION))

from replay_binary_contract import replay_binary_catalog  # noqa: E402


class BinaryTransportReplayTests(TestCase):
    def test_all_manifest_and_binary_dispositions_are_executed(self) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temporary)
        candidate = root / "candidate"
        candidate.write_bytes(b"candidate")
        candidate.chmod(0o700)

        def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
            request_path = Path(command[command.index("--request") + 1])
            fixture_root = Path(command[command.index("--fixture-root") + 1])
            output_path = Path(command[command.index("--output") + 1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            manifest_name = request["cases"][0]["arguments"]["returns"]["manifestFile"]
            manifest_path = fixture_root / "large" / manifest_name
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                request["cases"][0]["fixtureId"],
                manifest["fixtureId"],
            )
            file_name = manifest.get("fileName")
            generator = manifest.get("generator")
            payload_hex = (
                generator.get("payloadHex") if isinstance(generator, dict) else None
            )
            if (
                isinstance(payload_hex, str)
                and payload_hex
                and isinstance(file_name, str)
                and "/" not in file_name
                and "\\" not in file_name
            ):
                binary_path = fixture_root / "large" / "generated" / file_name
                self.assertTrue(binary_path.exists() or binary_path.is_symlink())
            if manifest_name == "manifest-non-finite-semantic.json":
                output_path.write_text(
                    json.dumps(
                        {
                            "schemaVersion": "s1.4x-result-batch-v1",
                            "requestId": request["requestId"],
                            "implementation": "scala-test",
                            "results": [
                                {
                                    "schemaVersion": "s1.4x-result-v1",
                                    "functionId": "cumulative_return",
                                    "fixtureId": request["cases"][0]["fixtureId"],
                                    "status": "error",
                                    "errorCode": "input_non_finite",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, b"", b"")
            code = (
                "binary_invalid"
                if manifest_name
                in {
                    "manifest-wrong-hash.json",
                    "manifest-truncated-binary.json",
                    "manifest-trailing-bytes.json",
                }
                else "manifest_invalid"
            )
            return subprocess.CompletedProcess(
                command,
                65,
                b"",
                json.dumps(
                    {
                        "schemaVersion": "s1.4x-transport-error-v1",
                        "code": code,
                        "requestId": request["requestId"],
                        "fixtureId": request["cases"][0]["fixtureId"],
                    }
                ).encode("utf-8"),
            )

        report = replay_binary_catalog(
            candidate=candidate,
            invalid_root=INVALID,
            output_directory=root / "replay",
            report_path=root / "report.json",
            runner=runner,
        )
        self.assertEqual(report["caseCount"], 12)
        self.assertEqual(report["transportFailureCount"], 11)
        self.assertEqual(report["semanticErrorCount"], 1)
        self.assertTrue(
            (
                root
                / "replay/manifest-symlink-escape/fixtures/large/generated/symlink-escape.f64le"
            ).is_symlink()
        )
