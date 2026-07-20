# S1.4X Scala/Haskell numeric parity research

S1.4X는 S1.4 production NumPy 11개 함수와 S1.4R NumPy/JAX 9개 함수의 수치·오류
계약을 Scala와 Haskell의 독립 process로 재현하기 위한 비생산 연구다. 이 subtree는
production `RiskEngine`, 공개 API, root `contracts/` 또는 다른 팀 workspace와 연결되지
않는다.

## 현재 상태

Gate 0 governance와 Gate 1 neutral fixture freeze 뒤 Scala/Haskell candidate 구현까지
완료했다.

- 언어 중립 JSON/binary exchange schema와 20개 함수·32개 stable error registry
- upstream Python/NumPy/JAX reference hash lock과 canonical fixture
- Scala A/B/C 및 Haskell `baseline-o0-fasm`/`optimized-o2-fasm` correctness
- Scala profile A와 Haskell `baseline-o0-fasm`의 historical selection evidence
- 6개 family, 89개 case, 3회 반복의 사전 동결 full benchmark plan

현재 HEAD에는 correctness·OCI·regression부터 87개 timing block과 typed finalization까지
한 번만 직렬 실행하는 detached full-run supervisor가 포함되어 있다. 실행 중 retry나
partial resume는 하지 않으며, 종료 후 봉인된 full evidence를 사람이 검토하기 전에는
공식 실험 순위나 `FINAL_PR_READY`를 주장하지 않는다.

[`reports/EXPEDITED_COMPLETION.md`](reports/EXPEDITED_COMPLETION.md)는 commit
`d91764ec7cafde75eb0cc5e140c9ff67046c5c88`의 historical 6/10 snapshot이다. 이
snapshot의 `sample-only`, `native-only`, `non-scoring` 결과는 현재 HEAD의 full
correctness·OCI·scorecard 또는 PR readiness를 뜻하지 않는다.

## 경계

- Python/NumPy/JAX source와 fixture는 읽기 전용 oracle이다.
- candidate가 Python/JAX를 실행·embed하거나 FFI/JNI/native extension, HTTP, gRPC로
  계산을 위임하는 것은 금지한다.
- canonical success는 finite Float64만 허용하고 `-0.0`을 `0.0`으로 정규화한다.
- small/paper case는 `rtol=1e-12`, `atol=1e-12`, large/property case는
  `rtol=1e-10`, `atol=1e-12`를 사용한다.
- tracked expected JSON bytes는 sidecar와 contract manifest로 exact hash-lock한다.
  `capture_reference_results.py --check`의 live 재생성은 libc `libm`의 ULP 차이를 숨기지
  않고 같은 typed tolerance로 판정하며, ID·순서·필드·정수·불리언·stable error는 exact다.
- generated `*.f64le`, local doctor evidence, tool output과 benchmark artifact는
  path-scoped ignore 대상이며 계약 입력이 아니다.

## 재현

repo root에서 실행한다.

```bash
S1_4X=workspaces/decision-platform/research/s1-4x-numeric-parity
CACHE_ROOT="$HOME/.cache/s1-4x"
mkdir -p "$CACHE_ROOT/tmp" "$CACHE_ROOT/uv"
export TMPDIR="$CACHE_ROOT/tmp"
export TEMP="$TMPDIR"
export TMP="$TMPDIR"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PYTHON=3.12.13

uv sync --frozen --project "$S1_4X/oracle"

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/generate_large_fixtures.py" --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/capture_reference_results.py" --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/capture_reference_results.py" \
    --request "$S1_4X/contract/fixtures/invalid/semantic-errors.v1.json" \
    --output "$S1_4X/contract/fixtures/invalid/semantic-errors.expected.v1.json" \
    --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/benchmarks/render_benchmark_plan.py" --check

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/benchmarks/run_rotated_blocks.py" validate-plan

uv run --frozen --project "$S1_4X/oracle" \
  python "$S1_4X/oracle/validate_contract.py" --check-all

uv run --frozen --project "$S1_4X/oracle" \
  pytest -q "$S1_4X/oracle/tests" "$S1_4X/benchmarks/tests"
```

`contract-manifest.v1.json`은 자기 자신을 제외한 immutable Gate 1 input의 hash
closure다. `referenceBaseCommit`은 S1.4X 파일이 생기기 전의 upstream commit이며,
향후 Gate 1 merge SHA를 lock에 다시 써 self-reference를 만들지 않는다. 최초 Gate 2
integration report가 immutable GitHub merge event의 Gate 1 SHA와 이 tree의
byte-identical 상태를 연결한다.

## 운영 경계

- 운영 후보는 기존 S1.4 Python/NumPy이며, S1.4R은 고급 리스크 수학 명세와 연구
  reference로 유지한다.
- S1.4X는 독립 parity oracle과 언어 설계·감사성·성능 연구에만 사용한다.
- S1.4X 결과가 좋아도 별도 migration ADR, 동일 경계 full benchmark, shadow/canary,
  observability와 rollback 승인이 없으면 Scala/Haskell을 production 경로에 넣지 않는다.
