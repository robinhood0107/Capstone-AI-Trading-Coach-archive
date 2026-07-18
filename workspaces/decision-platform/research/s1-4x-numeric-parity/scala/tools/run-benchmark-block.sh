#!/usr/bin/bash
set -euo pipefail

usage() {
  printf 'usage: %s --plan PLAN --block-dir BLOCK_DIR --qualification QUALIFICATION --boundary scala --selector SELECTOR --family FAMILY --rotation ROTATION --outer-repetition N --run-id RUN_ID --benchmark-subject-commit COMMIT\n' "$0" >&2
  exit 64
}

[[ "$#" -eq 20 ]] || usage
if [[ "$1" != "--plan" \
  || "$3" != "--block-dir" \
  || "$5" != "--qualification" \
  || "$7" != "--boundary" \
  || "$9" != "--selector" \
  || "${11}" != "--family" \
  || "${13}" != "--rotation" \
  || "${15}" != "--outer-repetition" \
  || "${17}" != "--run-id" \
  || "${19}" != "--benchmark-subject-commit" ]]; then
  usage
fi

# Shell/Python/JVM/Git 동작을 바꾸는 ambient hook은 sealed child에 전달하지 않는다.
while IFS= read -r environment_name; do
  case "$environment_name" in
    BASH_ENV | ENV | PYTHONPATH | PYTHONHOME | PYTHON* | VIRTUAL_ENV | \
      JAVA_TOOL_OPTIONS | JDK_JAVA_OPTIONS | _JAVA_OPTIONS | GIT_* | LD_*)
      unset "$environment_name"
      ;;
  esac
done < <(compgen -e)

export PATH="/usr/bin:/bin"
export LC_ALL="C"
export TZ="UTC"

REPO_ROOT="$(/usr/bin/git -C "$PWD" rev-parse --show-toplevel)"
if [[ "$REPO_ROOT" != /* || "$(/usr/bin/pwd -P)" != "$REPO_ROOT" ]]; then
  printf 'benchmark wrapper must run from the repository root\n' >&2
  exit 64
fi

SCALA_ROOT="$REPO_ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity/scala"
HELPER="$SCALA_ROOT/tools/scala_benchmark_block.py"
BENCHMARK_PYTHON="${S1_4X_BENCHMARK_PYTHON_BIN:?S1_4X_BENCHMARK_PYTHON_BIN is required}"
BENCHMARK_PYTHON_SHA256="${S1_4X_BENCHMARK_PYTHON_SHA256:?S1_4X_BENCHMARK_PYTHON_SHA256 is required}"
BENCHMARK_PYTHON_EXEC="${S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH:?S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH is required}"
SCALA_CLI="${S1_4X_SCALA_CLI_BIN:?S1_4X_SCALA_CLI_BIN is required}"
SCALAFIX="${S1_4X_SCALAFIX_BIN:?S1_4X_SCALAFIX_BIN is required}"
SCALAFMT_ARCHIVE="${S1_4X_SCALAFMT_ARCHIVE:?S1_4X_SCALAFMT_ARCHIVE is required}"
SCALAFMT="${S1_4X_SCALAFMT_BIN:?S1_4X_SCALAFMT_BIN is required}"
SELECTED_PROFILE="${S1_4X_SCALA_SELECTED_PROFILE_RESULT:?S1_4X_SCALA_SELECTED_PROFILE_RESULT is required}"
PROFILE_QUALIFICATION="${S1_4X_SCALA_QUALIFICATION_RESULT:?S1_4X_SCALA_QUALIFICATION_RESULT is required}"
CORRECTNESS_ROOT="${S1_4X_SCALA_CORRECTNESS_ROOT:?S1_4X_SCALA_CORRECTNESS_ROOT is required}"
JVM_ALLOWLIST="${S1_4X_SCALA_JVM_ALLOWLIST_RESULT:?S1_4X_SCALA_JVM_ALLOWLIST_RESULT is required}"
CACHE_ROOT="${S1_4X_CACHE_ROOT:?S1_4X_CACHE_ROOT is required}"
RUNTIME_HOME="${HOME:?HOME is required}"
JAVA_RUNTIME_HOME="${JAVA_HOME:?JAVA_HOME is required}"
JAVA_EXECUTABLE="$JAVA_RUNTIME_HOME/bin/java"
SCALA_CLI_SHA256="54b93b8401e333095526da5e4853780d5bf37494baa1ba5486e9e643084253d0"
SCALAFIX_SHA256="9db6db7359e580de8f4b72cd7c104d70023cf32a278db0c30aefb79c939eb0f3"
SCALAFMT_ARCHIVE_SHA256="e7d43a5621074a63a46d5b287d0b0bb0650033deeb836af2b27515b2127476f2"
SCALAFMT_SHA256="88526f9f4d64c2fb023d54578812419f49e2ec09e30e4fb77443a05f1a59cac0"
JAVA_EXECUTABLE_SHA256="ac3505f0c58282f00a6585591324a86b038c89cd171105fe42a1a0cf2f13b517"

verify_executable() {
  local label="$1"
  local executable="$2"
  local expected_sha256="$3"
  if [[ "$executable" != /* \
    || ! -f "$executable" \
    || ! -x "$executable" \
    || -L "$executable" \
    || "$(/usr/bin/realpath -e -- "$executable")" != "$executable" ]]; then
    printf '%s identity is unsafe\n' "$label" >&2
    exit 69
  fi
  if [[ "$(/usr/bin/sha256sum "$executable" | /usr/bin/awk '{print $1}')" \
    != "$expected_sha256" ]]; then
    printf '%s SHA-256 mismatch\n' "$label" >&2
    exit 69
  fi
}

verify_pinned_executable() {
  local label="$1"
  local executable="$2"
  local expected_sha256="$3"
  if [[ ! "$executable" =~ ^/proc/(self|[1-9][0-9]*)/fd/[0-9]+$ \
    || ! -f "$executable" \
    || ! -x "$executable" ]]; then
    printf '%s pinned FD identity is unsafe\n' "$label" >&2
    exit 69
  fi
  if [[ "$(/usr/bin/sha256sum "$executable" | /usr/bin/awk '{print $1}')" \
    != "$expected_sha256" ]]; then
    printf '%s pinned FD SHA-256 mismatch\n' "$label" >&2
    exit 69
  fi
}

verify_regular() {
  local label="$1"
  local path="$2"
  if [[ "$path" != /* \
    || ! -f "$path" \
    || -L "$path" \
    || "$(/usr/bin/realpath -e -- "$path")" != "$path" ]]; then
    printf '%s identity is unsafe\n' "$label" >&2
    exit 69
  fi
}

verify_directory() {
  local label="$1"
  local path="$2"
  if [[ "$path" != /* \
    || ! -d "$path" \
    || -L "$path" \
    || "$(/usr/bin/realpath -e -- "$path")" != "$path" ]]; then
    printf '%s identity is unsafe\n' "$label" >&2
    exit 69
  fi
}

verify_executable "benchmark Python" "$BENCHMARK_PYTHON" "$BENCHMARK_PYTHON_SHA256"
verify_pinned_executable \
  "benchmark Python" "$BENCHMARK_PYTHON_EXEC" "$BENCHMARK_PYTHON_SHA256"
verify_executable "Scala CLI" "$SCALA_CLI" "$SCALA_CLI_SHA256"
verify_executable "Scalafix" "$SCALAFIX" "$SCALAFIX_SHA256"
verify_executable "Scalafmt" "$SCALAFMT" "$SCALAFMT_SHA256"
verify_executable "Java executable" "$JAVA_EXECUTABLE" "$JAVA_EXECUTABLE_SHA256"
verify_regular "Scala helper" "$HELPER"
verify_regular "Scalafmt archive" "$SCALAFMT_ARCHIVE"
verify_regular "selected profile result" "$SELECTED_PROFILE"
verify_regular "profile qualification" "$PROFILE_QUALIFICATION"
verify_regular "JVM argument allowlist" "$JVM_ALLOWLIST"
verify_directory "runtime home" "$RUNTIME_HOME"
verify_directory "Java home" "$JAVA_RUNTIME_HOME"
verify_directory "cache root" "$CACHE_ROOT"
verify_directory "correctness root" "$CORRECTNESS_ROOT"
if [[ "$(/usr/bin/sha256sum "$SCALAFMT_ARCHIVE" | /usr/bin/awk '{print $1}')" \
  != "$SCALAFMT_ARCHIVE_SHA256" ]]; then
  printf 'Scalafmt archive SHA-256 mismatch\n' >&2
  exit 69
fi

exec /usr/bin/env -i \
  PATH="$JAVA_RUNTIME_HOME/bin:/usr/bin:/bin" \
  LANG="C.UTF-8" \
  LC_ALL="C.UTF-8" \
  TZ="UTC" \
  HOME="$RUNTIME_HOME" \
  JAVA_HOME="$JAVA_RUNTIME_HOME" \
  S1_4X_CACHE_ROOT="$CACHE_ROOT" \
  S1_4X_BENCHMARK_PYTHON_BIN="$BENCHMARK_PYTHON" \
  S1_4X_BENCHMARK_PYTHON_SHA256="$BENCHMARK_PYTHON_SHA256" \
  S1_4X_BENCHMARK_PYTHON_PINNED_FD_PATH="$BENCHMARK_PYTHON_EXEC" \
  S1_4X_SCALA_CLI_BIN="$SCALA_CLI" \
  S1_4X_SCALA_CLI_SHA256="$SCALA_CLI_SHA256" \
  S1_4X_SCALAFIX_BIN="$SCALAFIX" \
  S1_4X_SCALAFMT_ARCHIVE="$SCALAFMT_ARCHIVE" \
  S1_4X_SCALAFMT_BIN="$SCALAFMT" \
  S1_4X_SCALA_JAVA_BIN="$JAVA_EXECUTABLE" \
  S1_4X_SCALA_JAVA_SHA256="$JAVA_EXECUTABLE_SHA256" \
  S1_4X_SCALA_SELECTED_PROFILE_RESULT="$SELECTED_PROFILE" \
  S1_4X_SCALA_QUALIFICATION_RESULT="$PROFILE_QUALIFICATION" \
  S1_4X_SCALA_CORRECTNESS_ROOT="$CORRECTNESS_ROOT" \
  S1_4X_SCALA_JVM_ALLOWLIST_RESULT="$JVM_ALLOWLIST" \
  S1_4X_BENCHMARK_SUBJECT_COMMIT="${20}" \
  "$BENCHMARK_PYTHON_EXEC" "$HELPER" \
  --repo-root "$REPO_ROOT" \
  --plan "$2" \
  --block-dir "$4" \
  --qualification "$6" \
  --boundary "$8" \
  --selector "${10}" \
  --family "${12}" \
  --rotation "${14}" \
  --outer-repetition "${16}" \
  --run-id "${18}" \
  --benchmark-subject-commit "${20}"
