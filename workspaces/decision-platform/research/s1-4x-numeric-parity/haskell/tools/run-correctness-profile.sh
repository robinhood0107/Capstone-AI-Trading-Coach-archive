#!/usr/bin/bash
set -euo pipefail

usage() {
  echo "usage: run-correctness-profile.sh PROFILE --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
}

[[ "$#" -eq 3 && "$2" == "--output-dir" && "$3" == /* ]] || usage
case "$1" in
  baseline-o0-fasm | optimized-o2-fasm) ;;
  *) usage ;;
esac

if [[ -v STACK_YAML || -v STACK_ROOT || -v STACK_OPTS || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi
if [[ -e "$3" || -L "$3" ]]; then
  echo "correctness evidence output must be a new path" >&2
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

exec /usr/bin/python3 "$HASKELL_ROOT/tools/profile_workflow.py" correctness \
  --profile "$1" \
  --output-dir "$3"
