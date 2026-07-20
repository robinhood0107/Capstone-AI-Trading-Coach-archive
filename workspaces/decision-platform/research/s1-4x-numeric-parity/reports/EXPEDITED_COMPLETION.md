# S1.4X expedited completion evidence

Status: `EXPEDITED_PR_READY`

Evidence mode: `EXPEDITED_SAMPLE_ONLY / NATIVE_ONLY / NON_SCORING`

Completeness: `6/10` under the user-approved expedited plan A

## 범위와 subject

- Issue: `#26`
- base `origin/main`: `49921c4dbf5ef66407211b7f0f56218ab563043d`
- accepted historical evidence cutoff:
  `01bfbaa57fdceeddbaa6f6b113e95358349f0c42`
- expedited 승인 시 continuation HEAD:
  `747176ef62c53354eb104277b1b48d2112e56b1c`
- branch: `experiment/s1-4x-numeric-parity-20260718T131308Z`
- benchmark plan SHA-256:
  `9c8e9bb050a501b1ffc4f6db43a5ed3f4014a18c372feee29269b4626e11881d`
- selected profiles: Scala `A`, Haskell `baseline-o0-fasm`

cutoff 이후 변경은 Docker host validator의 quiet retry와 그 회귀 테스트, 갱신된 contract
manifest뿐이다. Scala/Haskell numeric core, stable-error contract, frozen fixture,
tolerance, selected profile와 benchmark case 구현은 바뀌지 않았다.
Sample의 numeric source는 cutoff와 continuation worktree가 동일함을 먼저 확인했다.
Haskell은 cutoff에서 빌드된 selected binary를 재사용했고, Scala는 동일 source와
selected profile A를 continuation worktree의 smoke wrapper로 실행했다.

## 재사용한 historical evidence

사용자 승인에 따라 T0~T5는 완료된 historical evidence로 재사용했으며 full gate를 다시
실행하지 않았다.

| Evidence | 결과 | SHA-256 |
|---|---:|---|
| Scala profile A correctness | PASS, mismatch 0 | `8ec59316b364337d6da87f97fa7bb95cb63487d5886efa007fded4133735d3ed` |
| Scala profile B correctness | PASS, mismatch 0 | `98c23b9c0d032cc3b4b05de3aae03bb6725858c224f7aa075d0864bb9235d200` |
| Scala profile C correctness | PASS, mismatch 0 | `0d997761c9e5812e5e9ffcd32b8b3846dcfd771e46469ada25fe023d24325b4c` |
| Scala qualification | PASS, profile A selected | `c4b40772aea7a833b32edd3a0a867f71ba74866777ee5e5a3e6228019f22885e` |
| Scala selected profile | A | `1b06dd29b131ece08deb52b62944c71a76d5f6fe93dee82b9bb538fcdb32ca52` |
| Haskell baseline correctness | PASS, mismatch 0 | `cb78ce728cbe4d93864c0e0e05c9c302809fecc7e872a39aabc201dad3d7fe1f` |
| Haskell optimized correctness | PASS, mismatch 0 | `eeae56bd72b744c79c66b24bf6f639af7b1b9280550db6e4bdf6f4e84219249d` |
| Haskell qualification | PASS, proven fallback | `996c99ec659b67fe9b38ca77ae59a3d696e79903b3c443d8a42ec52c7137c764` |
| Haskell selected profile | `baseline-o0-fasm` | `824ff9c82a516a6cfd762d01ec512aaa0de76587e3b86cd126f0668f224c9d50` |
| Haskell module safety | PASS | `309caac654cd24b3798112c2a40d756371e6a794dd3f2ce568c24424f515143c` |

Historical aggregate log SHA-256:
`e3650990ee06b794adbd3fd7d846872a5dc0c8dd912dded7db91e3716ed8299e`.

## Fresh focused validation

2026-07-20에 다음 최소 검증만 새로 실행했다.

| 검증 | 결과 |
|---|---|
| `pytest -q tests/test_validate_environment.py` | 20 passed |
| `ruff check validate_environment.py tests/test_validate_environment.py` | PASS |
| `validate_contract.py --check-all` | PASS, manifest 96 files, 20 functions |
| canonical 20-function Scala/Haskell replay | PASS, 20 functions / 41 results, oracle/Scala/Haskell mismatch 0 |

Fresh contract report SHA-256:
`274f3e1fb2fbcb8262ac90a6f15e5a8dc2d13eda2aa3f9d1e7550942b53376c4`.
Canonical comparison SHA-256:
`2d2da7c6595b9f01274da72e2b52f4854a13d61e9339c1764508810e67868b3f`.

## Native sample

공통 조건은 outer repetition 1회, CPU `0`, thread `1`이다. Scala는 JMH smoke
`fork=1`, warmup `1 x 200ms`, measurement `1 x 200ms`를 사용했다. Haskell은
Criterion `--time-limit 1`과 `+RTS -N1 -RTS`를 사용했다.

- UTC 실행 구간: `2026-07-20T14:02:15Z` ~ `2026-07-20T14:09:45Z` (450초)
- Scala toolchain: Scala CLI 1.15.0, Scala 3.8.4, Temurin 25.0.3+9-LTS
- Haskell toolchain: GHC 9.10.3, Criterion 1.6.4.0, `-O0 -fasm`
- Haskell benchmark binary SHA-256:
  `f46bcaeba0b2b50ba8337ff35355e2b1ef2cd57347053182a0822859267f73c4`

실행 argv 형태는 다음과 같다. `<case>`는 아래 표의 세 case ID를 순서대로 대입했다.

```text
taskset -c 0 scala/tools/run-jmh-native-smoke.sh \
  --plan benchmarks/benchmark-plan.v1.json --profile A \
  --case-id <case> --mode smoke --output-dir <new-output>

taskset -c 0 s1-4x-haskell-benchmark \
  --time-limit 1 --json <new-output>/native.json \
  --match glob <case-1> <case-2> <case-3> +RTS -N1 -RTS
```

| Candidate | Case | 관측값 | raw native SHA-256 |
|---|---|---:|---|
| Scala A | `path-transform/log_returns/n100000/b1` | 4,632,059.043478261 ns/logical-op | `0306d6ec9697aeb8e60e7c9f0ca722c027223d03be20d5c65edd371bedfef7d6` |
| Scala A | `probabilistic-scalar/probabilistic_sharpe_ratio/b16384` | 43.312638445598324 ns/logical-op | `aa4cb986e3f912a01c05870acaf224ce3a93d5122cb34ebcc7129cb90665b5d4` |
| Scala A | `coverage-batch/christoffersen_conditional_coverage/n100000/b32` | 4,480,349.375 ns/logical-op | `b88e9b915a4f9110e71ed4e9c6f1e2cdee5b1fb5842a79b2b30304a894a8615d` |
| Haskell `baseline-o0-fasm` | `path-transform/log_returns/n100000/b1` | 0.008900289180855791 s | `f85c703646eaf9bf5d56afec079e13d3cada3c2193f5367f0f97b601e5bc34e8` |
| Haskell `baseline-o0-fasm` | `probabilistic-scalar/probabilistic_sharpe_ratio/b16384` | 0.017463244025483292 s | `f85c703646eaf9bf5d56afec079e13d3cada3c2193f5367f0f97b601e5bc34e8` |
| Haskell `baseline-o0-fasm` | `coverage-batch/christoffersen_conditional_coverage/n100000/b32` | 0.5515866953540657 s | `f85c703646eaf9bf5d56afec079e13d3cada3c2193f5367f0f97b601e5bc34e8` |

Raw evidence는 ignored local cache의
`<S1_4X_CACHE_ROOT>/codex-runs/expedited-sample-20260720T140215Z/`에 있으며 tracked
artifact가 아니다. Scala evidence manifest SHA-256은
`e9c39dac6dd8eb107c5c6ff525492f9bb1dee3f58b59a6d901391b06941c8268`,
Haskell evidence manifest SHA-256은
`70b6c84af4d0acf458546bfe5bb99589992d8a20f3bef4618de9818d09a9d8e5`다.

## 제한과 waiver

- fresh Docker doctor와 OCI build/runtime replay를 실행하지 않았다.
- Scala A/B/C 및 Haskell O0/O2 full qualification을 재실행하지 않았다.
- GHC 9.14.1, Python/NumPy/JAX, S1.4/S1.4R full regression을 재실행하지 않았다.
- 89 cases x 3 outer repetitions, 6-domain scorecard와 family rotation을 실행하지 않았다.
- continuation HEAD에서 historical Haskell qualification을 strict 재계산하면
  `HASKELL_PROFILE_WORKFLOW_FAIL:COMMAND_ARGV_DRIFT`다. numeric candidate 변경이 아니라
  host-validator argv 변경으로 생긴 historical-evidence drift이며 이번 승인에서
  full requalification을 생략했다.
- 이 6개 관측값으로 dispersion, p95, 신뢰구간, aggregate score, 언어 순위나 승자를
  주장하지 않는다.
- Scala와 Haskell의 sample harness 및 보고 단위가 다르므로 표의 숫자로 candidate 간
  speedup ratio를 계산하지 않는다.
- 로컬 continuous-execution prompt는 `v3.3-expedited`이며 ignored/untracked다. 이
  local-only 상태는 PR evidence가 아니다.

따라서 이 보고서는 deferred full 89-case plan과 비교할 수 없고, production migration
근거가 아니다.

## S1.4 / S1.4R / S1.4X 사용 판단

- Production: S1.4 Python/NumPy 11함수를 지정된 운영 core로 유지한다.
- Research/specification: S1.4R NumPy를 고급 리스크 9함수의 수학 명세로 사용한다.
- Experiment: S1.4X는 독립 parity oracle, 언어 설계·감사성·성능 연구에만 사용한다.

이 판단은 sample 속도 비교가 아니라 현재 공개 architecture와 ADR-027의 비생산 경계,
운영 통합 비용을 근거로 한다. S1.4도 아직 endpoint/orchestrator에 연결된 배포 서비스가
아니므로 여기서는 “지정된 production core”로만 표현한다.
