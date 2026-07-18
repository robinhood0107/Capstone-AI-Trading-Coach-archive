# S1.4X integration gate

이 디렉터리는 frozen Gate 1 입력을 수정하지 않고 Python oracle, Scala candidate,
Haskell candidate를 같은 correctness·benchmark 계약에 연결한다. Candidate process는
절대 경로 request, fixture root, output만 받고 Python/JAX나 다른 candidate를 호출하지
않는다.

## 계약 요약

Correctness gate는 다음 수량과 순서를 exact set으로 검사한다.

- 함수 registry: candidate별 20/20
- stable error registry: S1.4 19개 + S1.4R 13개
- property: 25개 × 24개 seed × seed당 최소 42회 성공
- property당 최소 성공 횟수: 1,008회
- 비교 방향: oracle-vs-Scala, oracle-vs-Haskell, Scala-vs-Haskell
- 허용 mismatch: 세 방향 모두 0

Canonical fixture만 맞는 구현은 통과하지 않는다. Semantic error, invalid request,
binary manifest, finite value, negative zero, result ID·순서, process exit와 atomic output을
각각 검사한다.

## Correctness 실행

두 candidate와 frozen oracle의 canonical·semantic·transport·binary replay를 실행한다.
출력 디렉터리는 절대 경로여야 하고 실행 전 존재하면 안 된다.

```bash
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
RUN_PARENT="$(mktemp -d)"

"$S1_4X/integration/tools/run-integration-correctness.sh" \
  "$RUN_PARENT/integration-correctness"
```

언어 profile, property evidence, GHC compatibility, OCI runtime과 기존 Python 회귀까지
직렬로 실행하려면 다음 aggregate gate를 사용한다. 이 명령은 compiler와 container를
실행하는 heavy gate다.

```bash
"$S1_4X/integration/tools/run-native-oci-regression-gates.sh" \
  "$RUN_PARENT/native-oci-regression"
```

Aggregate gate의 순서는 contract/fixture 검증, Scala profile, Haskell profile 및
GHC 9.14.1 non-scoring replay, candidate별 property evidence, cross-language correctness,
network-disabled OCI replay, S1.4 production과 S1.4R regression이다. 앞 단계가 실패하면
뒤 단계를 성공으로 간주하지 않는다.

## Property evidence

`coverage_execution.py`는 ScalaCheck 또는 QuickCheck wrapper의 실제 subprocess receipt와
세 JSON report의 byte hash를 묶는다. `coverage_gate.py`는 이 report를 frozen
`property-plan.v1.json`, `function-registry.v1.json`, `error-registry.v1.json`에 다시
대조한다.

통과 조건은 다음과 같다.

- 25개 property ID와 frozen 순서가 exact
- 24개 seed가 각 property에 모두 실행됨
- seed별 성공 42회 이상, property별 성공 1,008회 이상
- 20개 function ID와 32개 error code가 exact
- dynamic error 29개와 static/reference-model error 3개가 exact
- runner, source closure, plan, seed corpus SHA-256가 모두 결속됨

## Benchmark 전 smoke

Smoke는 selector, fixture decode, native entrypoint와 forced evaluation이 연결되는지만
확인한다. 점수 산정 입력이 아니며 full benchmark를 대체하지 않는다.

Python 네 boundary의 non-scoring smoke:

```bash
"$S1_4X/integration/tools/run-python-benchmark-smoke.sh" \
  "$RUN_PARENT/python-smoke"
```

Official timing으로 진행하려면 Python 네 boundary와 Scala/Haskell의 frozen selector
전체가 smoke에서 통과해야 한다. `smallest` CI input도 같은 성격의 연결 검사일 뿐
`scorecard.v1.json`을 만들 수 없다.

## Full benchmark 계약

Frozen plan `benchmarks/benchmark-plan.v1.json`은 다음 closure를 고정한다.

- execution boundary 6개: Python/NumPy S1.4, Python/NumPy S1.4R,
  Python/JAX eager S1.4R, Python/JAX JIT S1.4R, Scala, Haskell
- function family 6개
- Scala/Haskell candidate case 89개
- outer repetition 3회
- rotated family block 87개
- CPU set 0, thread 1
- total timeout 32,400초

Scala는 JMH 1.37 AverageTime raw JSON을, Haskell은 Criterion 1.6.4.0 family raw JSON을
남긴다. Integration validator는 raw sample, fork/iteration, p95, confidence interval,
dispersion, logical-operation normalization을 report와 다시 계산해 비교한다. Haskell의 한
family raw와 receipt는 selector 안의 모든 case가 같은 path와 hash를 공유해야 한다.

### 1. Command manifest 생성

Command manifest는 shell-free argv, 실행 파일 절대 경로와 SHA-256, subject commit을
고정한다. 생성 시 `benchmarkSubjectCommit`은 현재 `HEAD`와 같아야 한다.

```bash
ORACLE="$S1_4X/oracle"
PLAN="$S1_4X/benchmarks/benchmark-plan.v1.json"
SUBJECT="$(git -C "$ROOT" rev-parse HEAD)"
BENCH_PARENT="$(mktemp -d)"
SCALA_BLOCK_WRAPPER="${SCALA_BLOCK_WRAPPER:?set the absolute Scala block wrapper path}"
HASKELL_BLOCK_WRAPPER="${HASKELL_BLOCK_WRAPPER:?set the absolute Haskell block wrapper path}"

TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
  /home/pjjpj/.local/bin/uv run --frozen --no-config --project "$ORACLE" \
  python "$S1_4X/integration/prepare_benchmark_commands.py" \
  --repo-root "$ROOT" \
  --benchmark-subject-commit "$SUBJECT" \
  --host-wrapper "$S1_4X/integration/tools/run-host-validator.sh" \
  --python-wrapper "$S1_4X/integration/tools/run-python-benchmark-block.sh" \
  --scala-wrapper "$SCALA_BLOCK_WRAPPER" \
  --haskell-wrapper "$HASKELL_BLOCK_WRAPPER" \
  --uv /home/pjjpj/.local/bin/uv \
  --output "$BENCH_PARENT/commands.json" \
  --sidecar "$BENCH_PARENT/commands.sha256"
```

생성기는 기존 manifest나 sidecar를 덮어쓰지 않는다. Runner는 manifest digest,
wrapper byte hash와 `benchmarkSubjectCommit == candidateSourceCommit`을 다시 검사한다.

### 2. Rotated blocks 실행

```bash
COMMAND_SHA256="$(awk '{print $1}' "$BENCH_PARENT/commands.sha256")"

TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
  uv run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/run_rotated_blocks.py" run \
  --plan "$PLAN" \
  --commands "$BENCH_PARENT/commands.json" \
  --commands-sha256 "$COMMAND_SHA256" \
  --benchmark-subject-commit "$SUBJECT" \
  --candidate-source-commit "$SUBJECT" \
  --output-root "$BENCH_PARENT/run" \
  --run-id "s1-4x-full-local" \
  --repo-root "$ROOT"
```

각 block은 timing 전에 host validator와 timeout qualification을 통과해야 한다. Partial
block, 임의 retry, `NOT_MEASURED`, selector 밖 case, 다른 executable 또는 stale input
ledger는 finalization에서 거부된다.

## Typed final candidate audit

`final_candidate_audit.py`는 사람이 입력한 점수를 받지 않는다. Scala와 Haskell 각각에
대해 다음 evidence ID와 source 역할을 exact하게 요구한다.

| Evidence ID | 필수 source 역할 |
|---|---|
| `correctness-contract` | `integration-coverage` |
| `property-coverage` | `integration-coverage` |
| `cross-language-parity` | `canonical-comparison`, `semantic-comparison` |
| `regressions` | `production-regression`, `research-regression` |
| `oci-correctness` | `oci-correctness` |
| `toolchain-reproducibility` | `toolchain-reproducibility` |
| `fixture-reproducibility` | `fixture-reproducibility` |
| `offline-runtime-reproducibility` | `offline-runtime-reproducibility` |
| purity 4개 rubric | evidence별 `rubric-assessment` |
| maintainability 5개 rubric | evidence별 `rubric-assessment` |
| integration fit 3개 rubric | evidence별 `rubric-assessment` |

각 source는 evidence ID별 고정 schema와 필드를 가져야 한다. Generic `status: PASS` JSON,
잘못된 역할, stale hash, 절대 경로, path traversal, leaf·중간 symlink, self-review rubric,
다른 subject commit은 거부된다.

감사 subject 뒤에 evidence/report 문서를 커밋한 다음 validator를 재실행할 수 있다. 단,
subject는 현재 `HEAD`의 ancestor여야 하고 worktree·index·untracked 상태는 깨끗해야 한다.
`subject..HEAD`에는 S1.4X `reports/` 아래 `.json`·`.md`·`.sha256` 파일과 S1.4X 루트
`README.md`, `integration/README.md`의 추가·수정만 허용한다. 삭제·이름 변경·파일 타입
변경과 candidate·lock·contract·oracle·benchmark·integration code 변경은 모두 거부된다.

실제 evidence envelope를 `<audit-root>/evidence/{scala,haskell}/`에 준비한 뒤 ledger를
생성하고 다시 검증한다. 출력 파일은 실행 전에 존재하면 안 된다.

```bash
AUDIT_ROOT="$BENCH_PARENT/final-audit"

TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
  uv run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/final_candidate_audit.py" generate \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$SUBJECT" \
  --evidence-root "$AUDIT_ROOT/evidence" \
  --output "$AUDIT_ROOT/final-candidate-audit.json"

TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
  uv run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/final_candidate_audit.py" validate \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$SUBJECT" \
  --ledger "$AUDIT_ROOT/final-candidate-audit.json"
```

검증이 끝난 뒤에만 고정 점수를 도출한다: correctness 35, purity/auditability 20,
reproducibility 15, maintainability 10, integration fit 5. Performance 15점은 full
benchmark result에서 별도로 계산한다.

## Finalization

Finalizer는 87개 block completeness, native raw statistics, timeout 분류, input ledger,
host identity, typed audit를 한 번 더 검증하고 네 portable report를 쓴다.

```bash
TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
  uv run --frozen --project "$ORACLE" \
  python "$S1_4X/integration/finalize_benchmark_run.py" \
  --plan "$PLAN" \
  --run-directory "$BENCH_PARENT/run/s1-4x-full-local" \
  --output-directory "$BENCH_PARENT/final-reports" \
  --benchmark-subject-commit "$SUBJECT" \
  --audit-ledger "$AUDIT_ROOT/final-candidate-audit.json"
```

생성되는 report:

- `benchmark-summary.v1.json`
- `benchmark-host-ledger.v1.json`
- `benchmark-raw-hash-manifest.v1.json`
- `scorecard.v1.json`

정상 full run은 `scheduledBlockCount == completedBlockCount == 87`,
`partialBlockCount == 0`, `notMeasuredCount == 0`이어야 한다. Frozen performance timeout
정책에 맞는 typed timeout만 `PASS_WITH_VALID_PERFORMANCE_TIMEOUTS`로 남을 수 있다.

## 경량 개발 검증

다음 명령은 compiler, container, full correctness와 timing을 실행하지 않는다.

```bash
(
  cd "$ORACLE"
  TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
    uv run --frozen python -m unittest discover \
    -s ../integration/tests -p 'test_*.py'
  TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
    uv run --frozen ruff check . ../benchmarks ../integration
  TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
    uv run --frozen mypy . ../benchmarks
  TMPDIR=/tmp TEMP=/tmp TMP=/tmp \
    uv run --frozen mypy --strict --explicit-package-bases \
    --follow-imports=silent --disable-error-code unused-ignore \
    ../integration/*.py
)
```

## 실패 원인 찾기

- `*_OUTPUT_ALREADY_EXISTS`: 새 absolute output 경로를 사용한다. 산출물을 덮어쓰지 않는다.
- `FINAL_AUDIT_SUBJECT_INVALID`: 요청한 40자리 commit이 `HEAD`의 ancestor인지, 이후
  변경이 위 report/README allowlist뿐인지, worktree·index·untracked 상태가 깨끗한지 확인한다.
- `FINAL_AUDIT_EVIDENCE_INVALID`: evidence ID의 source 역할, schema, hash, subject와
  candidate를 함께 확인한다.
- `BENCHMARK_*_INCOMPLETE`: 누락 block을 임의로 보충하지 말고 해당 run 전체를 폐기한 뒤
  correctness부터 다시 실행한다.
- `NATIVE_EXECUTION_*`: receipt argv, selected profile, toolchain lock, executable hash가
  frozen plan과 같은지 확인한다.
- `HOST_VALIDITY_*`: load, memory, disk, external process 또는 container 조건을 사용자가
  정리한 뒤 새 run으로 시작한다. Gate가 다른 process를 종료하지는 않는다.

## Production isolation

S1.4X는 `workspaces/decision-platform/research/s1-4x-numeric-parity/`와 전용 workflow에
격리된다. Integration code는 기존 S1.4 production 또는 S1.4R source를 수정하지 않고,
읽기 전용 regression·benchmark boundary로만 호출한다. Root contract/API/OpenAPI와 다른
팀 workspace에도 변경을 만들지 않는다. 이 subtree와 전용 workflow를 제거해도 production
runtime behavior는 달라지지 않아야 한다.
