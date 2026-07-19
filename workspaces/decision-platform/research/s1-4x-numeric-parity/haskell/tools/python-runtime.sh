#!/usr/bin/bash

# 이 파일은 source 전용이다. 실행 bytes는 retained FD로 고정하고 source path는
# CPython venv argv0 route의 full stat identity를 확인할 때만 다시 조회한다.
s1_4x_benchmark_python_route_identity() {
  local source_path="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
  local pinned_path="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:?S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH is required}"
  local resolved_source
  local source_identity
  local pinned_identity

  if [[ "$source_path" != /* \
    || ! -f "$source_path" \
    || -L "$source_path" \
    || ! -x "$source_path" ]]; then
    echo "benchmark Python source route identity is unsafe" >&2
    return 69
  fi
  resolved_source="$(/usr/bin/realpath -e -- "$source_path")" || {
    echo "benchmark Python source route resolution failed" >&2
    return 69
  }
  if [[ "$resolved_source" != "$source_path" \
    || ! "$pinned_path" =~ ^/proc/self/fd/([3-9]|[1-9][0-9]+)$ \
    || ! -f "$pinned_path" \
    || ! -x "$pinned_path" ]]; then
    echo "benchmark Python source and pinned FD route are unsafe" >&2
    return 69
  fi
  source_identity="$(
    /usr/bin/stat -Lc '%d:%i:%s:%f:%y:%z:%h' -- "$source_path"
  )" || {
    echo "benchmark Python source route stat failed" >&2
    return 69
  }
  pinned_identity="$(
    /usr/bin/stat -Lc '%d:%i:%s:%f:%y:%z:%h' -- "$pinned_path"
  )" || {
    echo "benchmark Python pinned FD route fstat failed" >&2
    return 69
  }
  if [[ "$source_identity" != "$pinned_identity" ]]; then
    echo "benchmark Python source route does not match the pinned FD" >&2
    return 69
  fi
  printf '%s\n' "$source_identity"
}

s1_4x_benchmark_python_venv_identity() {
  local source_path="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
  local bin_directory="${source_path%/*}"
  local venv_root="${bin_directory%/*}"
  local configuration="$venv_root/pyvenv.cfg"
  local resolved_configuration

  if [[ "$bin_directory" != "$venv_root/bin" \
    || ! -f "$configuration" \
    || -L "$configuration" ]]; then
    echo "benchmark Python external venv layout is unsafe" >&2
    return 69
  fi
  resolved_configuration="$(
    /usr/bin/realpath -e -- "$configuration"
  )" || {
    echo "benchmark Python pyvenv.cfg resolution failed" >&2
    return 69
  }
  if [[ "$resolved_configuration" != "$configuration" ]]; then
    echo "benchmark Python pyvenv.cfg route is unsafe" >&2
    return 69
  fi
  /usr/bin/stat -Lc '%d:%i:%s:%f:%y:%z:%h' -- "$configuration"
}

s1_4x_prepare_benchmark_python_environment() {
  local environment_name

  if [[ -v BASH_ENV || -v ENV ]]; then
    echo "ambient shell startup injection is forbidden" >&2
    return 69
  fi
  while IFS= read -r environment_name; do
    case "$environment_name" in
      PYTHON* | VIRTUAL_ENV)
        unset "$environment_name"
        ;;
    esac
  done < <(compgen -e)
  export PYTHONDONTWRITEBYTECODE=1
}

s1_4x_run_benchmark_python_binary_probe() {
  local before_identity
  local after_identity
  local child_status=0

  before_identity="$(s1_4x_benchmark_python_route_identity)" || return "$?"
  (
    s1_4x_prepare_benchmark_python_environment || exit "$?"
    exec -a "$S1_4X_BENCHMARK_PYTHON_BIN" \
      "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$@"
  ) || child_status=$?
  after_identity="$(s1_4x_benchmark_python_route_identity)" || return "$?"
  if [[ "$after_identity" != "$before_identity" ]]; then
    echo "benchmark Python source route changed during execution" >&2
    return 69
  fi
  return "$child_status"
}

s1_4x_run_benchmark_python() {
  local before_source_identity
  local before_venv_identity
  local after_source_identity
  local after_venv_identity
  local child_status=0

  before_source_identity="$(
    s1_4x_benchmark_python_route_identity
  )" || return "$?"
  before_venv_identity="$(
    s1_4x_benchmark_python_venv_identity
  )" || return "$?"
  (
    s1_4x_prepare_benchmark_python_environment || exit "$?"
    exec -a "$S1_4X_BENCHMARK_PYTHON_BIN" \
      "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$@"
  ) || child_status=$?
  after_source_identity="$(
    s1_4x_benchmark_python_route_identity
  )" || return "$?"
  after_venv_identity="$(
    s1_4x_benchmark_python_venv_identity
  )" || return "$?"
  if [[ "$after_source_identity" != "$before_source_identity" \
    || "$after_venv_identity" != "$before_venv_identity" ]]; then
    echo "benchmark Python venv route changed during execution" >&2
    return 69
  fi
  return "$child_status"
}

s1_4x_exec_benchmark_python() {
  s1_4x_benchmark_python_route_identity >/dev/null || return "$?"
  s1_4x_benchmark_python_venv_identity >/dev/null || return "$?"
  s1_4x_prepare_benchmark_python_environment || return "$?"
  exec -a "$S1_4X_BENCHMARK_PYTHON_BIN" \
    "$S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH" "$@"
}

s1_4x_pin_benchmark_python() {
  local source_path="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
  local expected_sha256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
  local pinned_path="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:-}"
  local before_identity
  local after_identity
  local actual_sha256
  local runtime_identity
  local venv_before_identity
  local venv_after_identity

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
    /usr/bin/stat -Lc '%d:%i:%s:%f:%y:%z:%h' -- "$pinned_path"
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
  before_identity="$(s1_4x_benchmark_python_route_identity)" || return "$?"
  runtime_identity="$(
    s1_4x_run_benchmark_python_binary_probe -I -S -c \
      'import os, sys
p = os.environ["S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH"]
source = os.environ["S1_4X_BENCHMARK_PYTHON_BIN"]
f = os.fstat(int(p.rsplit("/", 1)[1]))
e = os.stat("/proc/self/exe")
same = int((f.st_dev, f.st_ino, f.st_size) == (e.st_dev, e.st_ino, e.st_size))
source_bound = int(sys.executable == source)
print(
    sys.implementation.name,
    ".".join(map(str, sys.version_info[:3])),
    same,
    source_bound,
)'
  )" || {
    echo "benchmark Python pinned FD execution failed" >&2
    return 69
  }
  if [[ "$runtime_identity" != "cpython 3.12.13 1 1" ]]; then
    if [[ "$runtime_identity" == "cpython 3.12.13 "* ]]; then
      echo "benchmark Python executable does not match the pinned FD" >&2
      return 69
    fi
    echo "benchmark Python must be CPython 3.12.13" >&2
    return 69
  fi
  venv_before_identity="$(s1_4x_benchmark_python_venv_identity)" || return "$?"
  if ! s1_4x_run_benchmark_python -I -c \
    'import importlib.metadata
import os
import sys

import jsonschema
import numpy

source = os.environ["S1_4X_BENCHMARK_PYTHON_BIN"]
expected_prefix = os.path.dirname(os.path.dirname(source))
if (
    sys.executable != source
    or sys.prefix != expected_prefix
    or sys.base_prefix == sys.prefix
    or importlib.metadata.version("jsonschema") != "4.26.0"
    or importlib.metadata.version("numpy") != "2.5.1"
    or numpy.__version__ != "2.5.1"
):
    raise SystemExit("benchmark Python external venv dependency closure mismatch")'; then
    echo "benchmark Python external venv dependency closure failed" >&2
    return 69
  fi
  venv_after_identity="$(s1_4x_benchmark_python_venv_identity)" || return "$?"
  if [[ "$venv_after_identity" != "$venv_before_identity" ]]; then
    echo "benchmark Python pyvenv.cfg changed during validation" >&2
    return 69
  fi
  after_identity="$(s1_4x_benchmark_python_route_identity)" || return "$?"
  if [[ "$after_identity" != "$before_identity" ]]; then
    echo "benchmark Python source route changed during validation" >&2
    return 69
  fi
}
