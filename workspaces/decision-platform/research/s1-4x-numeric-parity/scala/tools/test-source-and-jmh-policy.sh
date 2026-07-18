#!/usr/bin/env bash
set -euo pipefail

SCALA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
S1_ROOT="$(cd -- "$SCALA_ROOT/.." && pwd -P)"
PLAN="$S1_ROOT/benchmarks/benchmark-plan.v1.json"
POLICY="$S1_ROOT/contract/scala-source-policy.v1.json"
temporary="$(mktemp -d -t s1-4x-scala-policy.XXXXXXXX)"

cleanup() {
  [[ "$temporary" == /tmp/s1-4x-scala-policy.* ]] || {
    printf 'refusing unsafe temporary cleanup: %s\n' "$temporary" >&2
    exit 1
  }
  rm -rf -- "$temporary"
}
trap cleanup EXIT

if "$SCALA_ROOT/tools/resolve-benchmark-case.sh" \
  --plan "$PLAN" \
  --boundary scala \
  --selector scala/path-transform \
  --family path-transform \
  --case-id path-transform/out_of_plan/n32/b1 \
  >"$temporary/out-of-plan.stdout" 2>"$temporary/out-of-plan.stderr"; then
  printf 'out-of-plan benchmark case unexpectedly passed\n' >&2
  exit 1
fi
grep -Fq 'outside frozen selector' "$temporary/out-of-plan.stderr"

python3 - "$PLAN" "$temporary/changed-arguments.json" changed-arguments <<'PY'
import json
import sys

source, destination, mutation = sys.argv[1:]
with open(source, encoding="utf-8") as stream:
    plan = json.load(stream, parse_float=str)
if mutation == "changed-arguments":
    target = next(item for item in plan["cases"] if item["functionId"] == "cagr")
    target["functionArguments"]["periods_per_year"] = 253
else:
    raise SystemExit("unknown mutation")
with open(destination, "x", encoding="utf-8", newline="\n") as stream:
    json.dump(plan, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")
PY
if "$SCALA_ROOT/tools/check-jmh-plan-integrity.sh" \
  --plan "$temporary/changed-arguments.json" \
  >"$temporary/changed-arguments.stdout" 2>"$temporary/changed-arguments.stderr"; then
  printf 'changed benchmark arguments unexpectedly passed\n' >&2
  exit 1
fi
grep -Fq 'JMH plan SHA mismatch' "$temporary/changed-arguments.stderr"

python3 - "$PLAN" "$temporary/overflow-plan.json" overflow <<'PY'
import json
import sys

source, destination, mutation = sys.argv[1:]
with open(source, encoding="utf-8") as stream:
    plan = json.load(stream, parse_float=str)
if mutation == "overflow":
    target = next(
        item for item in plan["cases"] if item["familyId"] == "coverage-batch"
    )
    target["vectorLength"] = 2147483647
    target["batchSize"] = 2147483647
else:
    raise SystemExit("unknown mutation")
with open(destination, "x", encoding="utf-8", newline="\n") as stream:
    json.dump(plan, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")
PY
if "$SCALA_ROOT/tools/check-jmh-plan-integrity.sh" \
  --plan "$temporary/overflow-plan.json" \
  >"$temporary/overflow.stdout" 2>"$temporary/overflow.stderr"; then
  printf 'overflow benchmark plan unexpectedly passed\n' >&2
  exit 1
fi
grep -Fq 'JMH plan SHA mismatch' "$temporary/overflow.stderr"

invocation="$SCALA_ROOT/benchmarks/scala/ai/trading/coach/s14x/benchmark/BenchmarkInvocation.scala"
! grep -Fq 'Math.multiplyExact' "$invocation"
grep -Fq 'BigInt(planCase.vectorLength) * BigInt(planCase.batchSize)' "$invocation"
grep -Fq 'requestedCount.isValidInt' "$invocation"

mkdir -p "$temporary/scala"
cp -- "$SCALA_ROOT/project.scala" "$temporary/scala/project.scala"
cp -- "$SCALA_ROOT/selected-profile.scala" "$temporary/scala/selected-profile.scala"
cp -- "$SCALA_ROOT/source-inputs.v1.json" "$temporary/scala/source-inputs.v1.json"
cp -R -- "$SCALA_ROOT/src" "$temporary/scala/src"
cp -R -- "$SCALA_ROOT/benchmarks" "$temporary/scala/benchmarks"
printf 'final class ForbiddenJavaSource {}\\n' \
  >"$temporary/scala/benchmarks/ForbiddenJavaSource.java"

if python3 "$SCALA_ROOT/tools/check_source_policy.py" \
  --scala-root "$temporary/scala" \
  --policy "$POLICY" \
  --manifest "$temporary/scala/source-inputs.v1.json" \
  --output "$temporary/unexpected-policy-pass.json" \
  >"$temporary/non-scala.stdout" 2>"$temporary/non-scala.stderr"; then
  printf 'non-Scala benchmark source unexpectedly passed\n' >&2
  exit 1
fi
grep -Fq 'non-scala-source' "$temporary/non-scala.stderr"

printf '%s\n' \
  'SCALA_NEGATIVE_POLICY_TEST_PASS outOfPlan=REJECT changedArguments=REJECT overflow=REJECT nonScalaBenchmark=REJECT'
