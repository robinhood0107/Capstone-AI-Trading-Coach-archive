#!/usr/bin/bash

# 이 파일은 source 전용이다. standalone wrapper는 source path를 한 번 열어
# exact bytes를 FD로 고정하고, 상위 block이 소유한 FD는 재개방 없이 검증한다.
s1_4x_pin_benchmark_python() {
  local source_path="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
  local expected_sha256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
  local pinned_path="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:-}"
  local before_identity
  local after_identity
  local actual_sha256
  local runtime_identity

  if [[ "$source_path" != /* \
    || "$source_path" == *":"* \
    || "$source_path" == *"|"* \
    || "$source_path" == *$'\n'* \
    || "$source_path" == *"//"* \
    || "$source_path" == *"/./"* \
    || "$source_path" == *"/../"* \
    || "$source_path" == */. \
    || "$source_path" == */.. \
    || ! "$expected_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "benchmark Python source path layout or SHA-256 is invalid" >&2
    return 69
  fi

  if [[ -z "$pinned_path" ]]; then
    if [[ ! -f "$source_path" \
      || -L "$source_path" \
      || ! -x "$source_path" \
      || "$(/usr/bin/realpath -e -- "$source_path")" != "$source_path" ]]; then
      echo "benchmark Python source identity is unsafe" >&2
      return 69
    fi
    if ! exec {S1_4X_BENCHMARK_PYTHON_OWNER_FD}<"$source_path"; then
      echo "benchmark Python source could not be pinned" >&2
      return 69
    fi
    pinned_path="/proc/self/fd/$S1_4X_BENCHMARK_PYTHON_OWNER_FD"
    export S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH="$pinned_path"
  fi

  if [[ ! "$pinned_path" =~ ^/proc/self/fd/([3-9]|[1-9][0-9]+)$ \
    || ! -f "$pinned_path" \
    || ! -x "$pinned_path" ]]; then
    echo "benchmark Python pinned FD identity is unsafe" >&2
    return 69
  fi
  before_identity="$(
    /usr/bin/stat -Lc '%d:%i:%s:%f:%Y:%Z:%h' -- "$pinned_path"
  )" || {
    echo "benchmark Python pinned FD fstat failed" >&2
    return 69
  }
  actual_sha256="$(
    /usr/bin/sha256sum "$pinned_path" | /usr/bin/awk '{print $1}'
  )" || {
    echo "benchmark Python pinned FD SHA-256 failed" >&2
    return 69
  }
  if [[ "$actual_sha256" != "$expected_sha256" ]]; then
    echo "benchmark Python pinned FD SHA-256 mismatch" >&2
    return 69
  fi
  runtime_identity="$(
    "$pinned_path" -I -S -c \
      'import os, sys
p = os.environ["S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH"]
f = os.fstat(int(p.rsplit("/", 1)[1]))
e = os.stat("/proc/self/exe")
same = int((f.st_dev, f.st_ino, f.st_size) == (e.st_dev, e.st_ino, e.st_size))
print(sys.implementation.name, ".".join(map(str, sys.version_info[:3])), same)'
  )" || {
    echo "benchmark Python pinned FD execution failed" >&2
    return 69
  }
  if [[ "$runtime_identity" != "cpython 3.12.13 1" ]]; then
    if [[ "$runtime_identity" == "cpython 3.12.13 "* ]]; then
      echo "benchmark Python executable does not match the pinned FD" >&2
      return 69
    fi
    echo "benchmark Python must be CPython 3.12.13" >&2
    return 69
  fi
  after_identity="$(
    /usr/bin/stat -Lc '%d:%i:%s:%f:%Y:%Z:%h' -- "$pinned_path"
  )" || {
    echo "benchmark Python pinned FD post-check failed" >&2
    return 69
  }
  if [[ "$after_identity" != "$before_identity" ]]; then
    echo "benchmark Python pinned FD changed during validation" >&2
    return 69
  fi
}
