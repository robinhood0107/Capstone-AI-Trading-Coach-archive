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
CACHE_ROOT="${S1_4X_CACHE_ROOT:-$HOME/.cache/s1-4x}"
UV_BIN="${S1_4X_UV_BIN:?set the verified absolute uv executable path}"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/uv" "$CACHE_ROOT/coursier"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export COURSIER_CACHE="$CACHE_ROOT/coursier"
RUN_PARENT="$(mktemp -d "$TMPDIR/s1-4x-integration.XXXXXX")"

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

## Detached full-run supervisor

`detached_full_run.py`는 full gate를 foreground에서 한 번만 직렬 실행하고, tracked
launcher는 이 process를 transient user-systemd cgroup에 분리한다. Supervisor와 launcher는
benchmark를 재시작하거나 부분 결과를 이어 붙이지 않는다.

Terminal FAIL run은 immutable source evidence로만 보존한다. `SEALED_PREFIX_REUSE_V1`은
같은 run의 resume가 아니라 새 run root를 만드는 별도 실행 모드다. Parent terminal
SHA256SUMS, source commit ancestry, unchanged candidate/plan diff allowlist와 각 artifact
hash를 모두 통과한 완료 prefix만 import하며 failed partial qualification, 이전 selector
결과와 stale contract validation은 가져오지 않는다. Import 뒤 현재 contract를 새로
검증하고 Scala selector를 재계산한다. Haskell selector는 historical host argv drift로
재실행하지 않고 tracked selected-profile과 qualification/correctness SHA binding을
sealed import receipt에서 재검증한 다음 공통 tail을 실행한다.

실행 전에는 다음 계약을 모두 만족해야 한다.

- 현재 branch는 `experiment/s1-4x-numeric-parity*`이며 clean `HEAD`가 같은 origin remote
  ref에 non-force push되어 있어야 한다.
- run ID와 repository 밖의 새 absolute run root를 사용한다.
- `JAVA_HOME`과 `S1_4X_UV_BIN`, `S1_4X_DOCKER_BIN`,
  `S1_4X_DOCKER_SHA256`, `S1_4X_BENCHMARK_PYTHON_BIN`,
  `S1_4X_SCALA_CLI_BIN`, `S1_4X_SCALAFIX_BIN`,
  `S1_4X_SCALAFMT_ARCHIVE`, `S1_4X_SCALAFMT_BIN`,
  `S1_4X_GHCUP_BIN`, `S1_4X_STACK_BIN`,
  `S1_4X_AUTHORITATIVE_GHC_BIN`, `S1_4X_LATEST_GHC_BIN`,
  `S1_4X_HLINT_BIN`, `S1_4X_STYLISH_BIN`,
  `S1_4X_VECTOR_SOURCE_ARCHIVE`를 검증된 absolute path로 export한다.
- Windows/WSL 종료나 재부팅을 견디는 daemon은 아니다. User manager session과 benchmark
  host는 terminal marker가 생길 때까지 유지한다.

실행 명령은 다음과 같다.

```bash
ROOT="$(git rev-parse --show-toplevel)"
S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
SUBJECT="$(git -C "$ROOT" rev-parse HEAD)"
RUN_ID="s1-4x-full-$(date -u +%Y%m%dT%H%M%SZ)-${SUBJECT:0:8}"
RUN_ROOT="$HOME/.cache/s1-4x/detached-runs/$RUN_ID"
mkdir -p "$(dirname "$RUN_ROOT")"

"$S1_4X/integration/tools/launch-detached-full-run.sh" \
  --repo-root "$ROOT" \
  --run-root "$RUN_ROOT" \
  --run-id "$RUN_ID" \
  --subject "$SUBJECT"
```

봉인된 `20260720t1527z-e2d0a443` 실패 run에서 완료 prefix를 재사용하는 새 run은 네 source
인자를 모두 함께 명시한다.

```bash
"$S1_4X/integration/tools/launch-detached-full-run.sh" \
  --repo-root "$ROOT" \
  --run-root "$RUN_ROOT" \
  --run-id "$RUN_ID" \
  --subject "$SUBJECT" \
  --failed-run-root \
    "$HOME/.cache/s1-4x/detached-runs/20260720t1527z-e2d0a443" \
  --scala-qualification-source \
    "$HOME/.cache/s1-4x/codex-runs/native-oci-01bfbaa-final1/scala" \
  --haskell-static-source \
    "$HOME/.cache/s1-4x/codex-runs/native-oci-01bfbaa-final1/haskell" \
  --haskell-profile-source \
    "$HOME/.cache/s1-4x/codex-runs/haskell-p-a30bbca-final1"
```

이 sealed continuation에만 `S1_4X_IGNORE_AMBIENT_HOST_ACTIVITY=1`이 supervisor 내부에서
설정된다. 실행 컨테이너 수와 외부 Codex CPU 기준은 observe-only가 되고, Docker API,
disk/memory, affinity, normalized load gate는 그대로다. 일반 fresh run은 기존 정책을
그대로 적용한다.

실행 순서는 native/OCI/regression aggregate gate, exact command manifest 봉인, 87개 rotated
block, typed finalizer다. `run-plan.v1.json`과 sidecar, `events.jsonl`, 단계별
`stdout.log`·`stderr.log`·`receipt.json`, checkpoint, correctness·benchmark raw artifact,
portable final report, terminal evidence index와 SHA-256 manifest를 run root에 남긴다.

상태 확인은 marker만 읽는 다음 명령으로 한다.

```bash
/usr/bin/python3 "$S1_4X/integration/detached_full_run.py" status \
  --run-root "$RUN_ROOT"
UNIT="$(jq -r '.unitName' "$RUN_ROOT/run-plan.v1.json")"
systemctl --user show "$UNIT" \
  -p ActiveState -p SubState -p Result -p ExecMainStatus -p NRestarts
```

Marker가 아직 없으면 첫 명령만으로 active service와 marker 없이 죽은 service를 구분할 수
없으므로 exact unit 상태를 함께 확인한다.

`terminal/PASS.json`은 87개 block과 portable report가 review 가능한 상태라는 뜻이다.
이는 언어 순위, production migration 또는 `FINAL_PR_READY` 선언이 아니다. 종료 뒤 sealed
artifact와 scorecard를 검토하고 report-only commit, final audit와 remote SHA 검증을 별도로
마쳐야 한다. `terminal/FAIL.json` 또는 marker 없는 비정상 종료는 같은 run을 보충하지 않고
새 run 승인 대상으로 남긴다.

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
고정한다. 생성 시 `benchmarkSubjectCommit`은 현재 `HEAD`와 같아야 한다. Full run의
authoritative argv는 supervisor가 sealed `run-plan.v1.json`의 runtime binding과
correctness output에서 생성한다. Required executable 13개와 evidence 10개 중 하나라도
생략한 수동 manifest는 full run 입력이 아니다.

```bash
jq '.argv' "$RUN_ROOT/stages/command-sealing/receipt.json"
(cd "$RUN_ROOT/benchmark" && sha256sum -c commands.sha256)
```

직접 진단이 필요하면 이 receipt의 argv를 byte-identical하게 재사용한다. 생성기는 기존
manifest나 sidecar를 덮어쓰지 않는다. Runner는 manifest digest, wrapper byte hash와
`benchmarkSubjectCommit == candidateSourceCommit`을 다시 검사한다.

### 2. Rotated blocks 실행

Supervisor가 실행한 exact argv는
`stages/frozen-timing/receipt.json`에 남는다. 이 argv에는 command manifest SHA-256과
correctness stage가 만든 `large-fixtures` root 및 `large-fixture-receipt.json`이 모두
포함되어야 한다. 누락한 수동 timing은 valid block을 만들 수 없다.

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

"$UV_BIN" run --frozen --no-config --project "$ORACLE" \
  python "$S1_4X/integration/final_candidate_audit.py" generate \
  --repository-root "$ROOT" \
  --benchmark-subject-commit "$SUBJECT" \
  --evidence-root "$AUDIT_ROOT/evidence" \
  --output "$AUDIT_ROOT/final-candidate-audit.json"

"$UV_BIN" run --frozen --no-config --project "$ORACLE" \
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

Supervisor가 실행한 exact finalizer argv는
`stages/typed-finalization/receipt.json`에 남는다. 이 argv는 run directory, large fixture
root, exact subject와 correctness aggregate gate의 typed audit ledger를 함께 결속한다.

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
  "$UV_BIN" run --frozen --no-config python -m unittest discover \
    -s ../integration/tests -p 'test_*.py'
  "$UV_BIN" run --frozen --no-config ruff check . ../benchmarks ../integration
  "$UV_BIN" run --frozen --no-config mypy . ../benchmarks
  "$UV_BIN" run --frozen --no-config mypy --strict --explicit-package-bases \
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
