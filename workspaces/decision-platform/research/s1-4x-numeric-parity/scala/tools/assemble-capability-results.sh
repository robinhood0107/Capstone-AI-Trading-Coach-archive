#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?set exact Scala CLI 1.15.0 binary path}"
SCALAFIX="${S1_4X_SCALAFIX_BIN:?set exact Scalafix 0.14.7 binary path}"
SCALAFMT_BIN="${S1_4X_SCALAFMT_BIN:?set exact Scalafmt 3.11.4 binary path}"
HARD_COMPILER="${S1_4X_SCALA_HARD_COMPILER_RESULT:?set hard compiler result path}"
TOOLCHAIN_RESULT="${S1_4X_SCALA_TOOLCHAIN_RESULT:?set typed toolchain identity result path}"
SCALAFMT_RESULT="${S1_4X_SCALA_SCALAFMT_RESULT:?set Scalafmt result path}"
SEMANTIC="${S1_4X_SCALA_SEMANTIC_RESULT:?set semantic receipt path}"
SOURCE_POLICY="${S1_4X_SCALA_SOURCE_POLICY_RESULT:?set source-policy result path}"
JMH_RUN="${S1_4X_SCALA_JMH_SMOKE_RESULT:?set real JVM/JMH smoke result path}"
JVM_ALLOWLIST="${S1_4X_SCALA_JVM_ALLOWLIST_RESULT:?set smoke-produced JVM allowlist result path}"
CORRECTNESS_ROOT="${S1_4X_SCALA_CORRECTNESS_ROOT:?set A/B/C correctness root}"
QUALIFICATION="${S1_4X_SCALA_QUALIFICATION_RESULT:?set A/B/C qualification result path}"
OUTPUT_DIR=""

usage() {
  printf 'usage: %s --output-dir <new-absolute-directory>\n' "$0" >&2
  exit 64
}

[[ "${1:-}" == "--output-dir" && "$#" -eq 2 ]] || usage
OUTPUT_DIR="$2"
[[ "$OUTPUT_DIR" == /* && ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || usage

python3 "$SCALA_ROOT/tools/assemble_capability_results.py" \
  --scala-root "$SCALA_ROOT" \
  --plan "$S1_ROOT/contract/capability-smoke-plan.v1.json" \
  --benchmark-plan "$S1_ROOT/benchmarks/benchmark-plan.v1.json" \
  --source-policy-config "$S1_ROOT/contract/scala-source-policy.v1.json" \
  --source-manifest "$SCALA_ROOT/source-inputs.v1.json" \
  --compiler-profiles "$SCALA_ROOT/compiler-profiles.v1.json" \
  --toolchain-lock "$SCALA_ROOT/toolchain-lock.v1.json" \
  --merged-provenance "$S1_ROOT/contract/toolchain-provenance.v1.json" \
  --scala-cli-bin "$SCALA_CLI" \
  --scalafix-bin "$SCALAFIX" \
  --scalafmt-bin "$SCALAFMT_BIN" \
  --toolchain-result "$TOOLCHAIN_RESULT" \
  --hard-compiler "$HARD_COMPILER" \
  --scalafmt "$SCALAFMT_RESULT" \
  --semantic "$SEMANTIC" \
  --source-policy "$SOURCE_POLICY" \
  --jmh-run "$JMH_RUN" \
  --jvm-allowlist "$JVM_ALLOWLIST" \
  --correctness-root "$CORRECTNESS_ROOT" \
  --qualification "$QUALIFICATION" \
  --output-dir "$OUTPUT_DIR"
