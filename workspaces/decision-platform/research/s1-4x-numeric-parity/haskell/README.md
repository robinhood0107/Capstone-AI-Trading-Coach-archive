# S1.4X Haskell numeric parity lane

이 디렉터리는 동결된 S1.4/S1.4R 수치 계약을 Haskell로 독립 재구현하는 비생산
연구 lane이다. Python/JAX oracle이나 Scala candidate를 실행하거나 호출하지 않는다.

- authoritative compiler: GHC 9.10.3
- authoritative snapshot: Stackage LTS 24.50
- compatibility compiler: GHC 9.14.1 (non-scoring)
- process executable: `s1-4x-haskell`
- benchmark executable: `s1-4x-haskell-benchmark`

`src/S14X/Core`는 순수 계산 경계이며 `src/S14X/Contract`와 `app/Main.hs`만 파일,
JSON, binary manifest, atomic output을 다룬다. 빌드와 검증은 `tools/`의 fail-closed
wrapper를 통해 실행한다.
