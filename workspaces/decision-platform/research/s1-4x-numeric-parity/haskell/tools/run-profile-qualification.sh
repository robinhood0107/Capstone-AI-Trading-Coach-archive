#!/usr/bin/bash
set -euo pipefail

usage() {
  echo "usage: run-profile-qualification.sh --plan PLAN --profiles baseline-o0-fasm,optimized-o2-fasm --enforce-order-plan --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
}

[[ "$#" -eq 7 \
  && "$1" == "--plan" \
  && "$2" == /* \
  && "$3" == "--profiles" \
  && "$4" == "baseline-o0-fasm,optimized-o2-fasm" \
  && "$5" == "--enforce-order-plan" \
  && "$6" == "--output-dir" \
  && "$7" == /* ]] || usage

if [[ -v STACK_YAML || -v STACK_ROOT || -v STACK_OPTS || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi
if [[ -e "$7" || -L "$7" ]]; then
  echo "qualification evidence output must be a new path" >&2
  exit 73
fi

while IFS= read -r environment_name; do
  case "$environment_name" in
    BASH_ENV | ENV | PYTHONPATH | PYTHONHOME | VIRTUAL_ENV | \
      JAVA_TOOL_OPTIONS | JDK_JAVA_OPTIONS | _JAVA_OPTIONS | GIT_* | LD_* | \
      GHCRTS | GHC_ENVIRONMENT | HASKELL_PACKAGE_SANDBOX | CABAL_CONFIG | \
      HPACK_CONFIG)
      unset "$environment_name"
      ;;
  esac
done < <(compgen -e)

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null

exec /usr/bin/python3 "$HASKELL_ROOT/tools/profile_workflow.py" qualification \
  --plan "$2" \
  --profiles "$4" \
  --enforce-order-plan \
  --output-dir "$7"
