#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUNNER="$SCALA_ROOT/tools/run-property-evidence.sh"
TEST_TMP_ROOT="${S1_4X_TEST_TMP_ROOT:-${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}/tmp}"
mkdir -p "$TEST_TMP_ROOT"
TEST_TMP_ROOT="$(realpath -- "$TEST_TMP_ROOT")"
temporary="$(mktemp -d -p "$TEST_TMP_ROOT" s1-4x-scala-wrapper.XXXXXXXX)"

cleanup() {
  [[ "$temporary" == "$TEST_TMP_ROOT"/s1-4x-scala-wrapper.* ]] || {
    printf 'refusing unsafe temporary cleanup: %s\n' "$temporary" >&2
    exit 1
  }
  rm -rf -- "$temporary"
}
trap cleanup EXIT

fake_cli="$temporary/fake-scala-cli"
capture="$temporary/capture.json"
python3 - "$fake_cli" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(
    """#!/usr/bin/env bash
set -euo pipefail
python3 - "$S1_4X_FAKE_CAPTURE" "$@" <<'INNER'
import json
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(
    json.dumps(sys.argv[2:], ensure_ascii=False, separators=(",", ":")) + "\\n",
    encoding="utf-8",
)
INNER
""",
    encoding="utf-8",
)
path.chmod(0o755)
PY

output="$temporary/output"
S1_4X_SCALA_CLI_BIN="$fake_cli" \
S1_4X_FAKE_CAPTURE="$capture" \
  "$RUNNER" --output-dir "$output" >"$temporary/wrapper.stdout"

expected_command_sha="$(
  python3 - "$(readlink -f -- "$RUNNER")" --output-dir "$output" <<'PY'
import hashlib
import json
import sys

payload = json.dumps(
    sys.argv[1:],
    allow_nan=False,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
)"

python3 - "$capture" "$expected_command_sha" "$(readlink -f -- "$RUNNER")" <<'PY'
import json
import sys

capture_path, expected_sha, expected_runner = sys.argv[1:]
with open(capture_path, encoding="utf-8") as stream:
    arguments = json.load(stream)
separator = arguments.index("--")
inner = arguments[separator + 1 :]
options = dict(zip(inner[::2], inner[1::2], strict=True))
assert options["--command-argv-sha256"] == expected_sha
assert options["--runner-path"] == expected_runner
PY

printf 'SCALA_PROPERTY_WRAPPER_BINDING_PASS commandArgvSha256=%s\n' \
  "$expected_command_sha"
