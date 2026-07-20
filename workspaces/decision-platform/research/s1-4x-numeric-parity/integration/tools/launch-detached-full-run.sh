#!/usr/bin/env bash
set -euo pipefail

# Tracked launcher는 증거 producer를 user-systemd cgroup에 분리할 뿐 benchmark를 재시도하지 않는다.
ROOT=""
RUN_ROOT=""
RUN_ID=""
SUBJECT=""
FAILED_RUN_ROOT=""
SCALA_QUALIFICATION_SOURCE=""
HASKELL_STATIC_SOURCE=""
HASKELL_PROFILE_SOURCE=""

usage() {
  printf 'usage: %s --repo-root ABSOLUTE_REPO --run-root ABSOLUTE_NEW_ROOT --run-id ID --subject COMMIT [--failed-run-root ABS --scala-qualification-source ABS --haskell-static-source ABS --haskell-profile-source ABS]\n' "$0" >&2
  exit 64
}

while (($# > 0)); do
  case "$1" in
    --repo-root)
      (($# >= 2)) || usage
      ROOT="$2"
      shift 2
      ;;
    --run-root)
      (($# >= 2)) || usage
      RUN_ROOT="$2"
      shift 2
      ;;
    --run-id)
      (($# >= 2)) || usage
      RUN_ID="$2"
      shift 2
      ;;
    --subject)
      (($# >= 2)) || usage
      SUBJECT="$2"
      shift 2
      ;;
    --failed-run-root)
      (($# >= 2)) || usage
      FAILED_RUN_ROOT="$2"
      shift 2
      ;;
    --scala-qualification-source)
      (($# >= 2)) || usage
      SCALA_QUALIFICATION_SOURCE="$2"
      shift 2
      ;;
    --haskell-static-source)
      (($# >= 2)) || usage
      HASKELL_STATIC_SOURCE="$2"
      shift 2
      ;;
    --haskell-profile-source)
      (($# >= 2)) || usage
      HASKELL_PROFILE_SOURCE="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ "$ROOT" == /* && -d "$ROOT" && ! -L "$ROOT" ]] || usage
[[ "$RUN_ROOT" == /* && ! -e "$RUN_ROOT" && ! -L "$RUN_ROOT" ]] || usage
[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{0,47}$ ]] || usage
[[ "$SUBJECT" =~ ^[0-9a-f]{40}$ ]] || usage
CONTINUATION_SOURCE_COUNT=0
for source in \
  "$FAILED_RUN_ROOT" \
  "$SCALA_QUALIFICATION_SOURCE" \
  "$HASKELL_STATIC_SOURCE" \
  "$HASKELL_PROFILE_SOURCE"
do
  [[ -z "$source" ]] || ((CONTINUATION_SOURCE_COUNT += 1))
done
[[ "$CONTINUATION_SOURCE_COUNT" == 0 || "$CONTINUATION_SOURCE_COUNT" == 4 ]] || usage
case "$ROOT$RUN_ROOT$FAILED_RUN_ROOT$SCALA_QUALIFICATION_SOURCE$HASKELL_STATIC_SOURCE$HASKELL_PROFILE_SOURCE" in
  *[[:space:]%]*)
    echo "repository, run, and continuation paths cannot contain whitespace or percent" >&2
    exit 64
    ;;
esac
PARENT_RUN_ID="NONE"
if [[ "$CONTINUATION_SOURCE_COUNT" == 4 ]]; then
  for source in \
    "$FAILED_RUN_ROOT" \
    "$SCALA_QUALIFICATION_SOURCE" \
    "$HASKELL_STATIC_SOURCE" \
    "$HASKELL_PROFILE_SOURCE"
  do
    [[ "$source" == /* && -d "$source" && ! -L "$source" ]] || usage
  done
  PARENT_RUN_ID="${FAILED_RUN_ROOT##*/}"
fi

S1_4X="$ROOT/workspaces/decision-platform/research/s1-4x-numeric-parity"
SUPERVISOR="$S1_4X/integration/detached_full_run.py"
UNIT="s1-4x-full-$RUN_ID.service"
[[ -f "$SUPERVISOR" && ! -L "$SUPERVISOR" ]] || usage

for environment_name in \
  JAVA_HOME \
  S1_4X_UV_BIN \
  S1_4X_DOCKER_BIN \
  S1_4X_DOCKER_SHA256 \
  S1_4X_BENCHMARK_PYTHON_BIN \
  S1_4X_SCALA_CLI_BIN \
  S1_4X_SCALAFIX_BIN \
  S1_4X_SCALAFMT_ARCHIVE \
  S1_4X_SCALAFMT_BIN \
  S1_4X_GHCUP_BIN \
  S1_4X_STACK_BIN \
  S1_4X_AUTHORITATIVE_GHC_BIN \
  S1_4X_LATEST_GHC_BIN \
  S1_4X_HLINT_BIN \
  S1_4X_STYLISH_BIN \
  S1_4X_VECTOR_SOURCE_ARCHIVE
do
  [[ -n "${!environment_name:-}" ]] || {
    printf 'required environment is missing: %s\n' "$environment_name" >&2
    exit 64
  }
done

PREPARE_ARGS=(
  prepare
  --repo-root "$ROOT"
  --run-root "$RUN_ROOT"
  --run-id "$RUN_ID"
  --benchmark-subject-commit "$SUBJECT"
  --overall-timeout-seconds 61200
)
if [[ "$CONTINUATION_SOURCE_COUNT" == 4 ]]; then
  PREPARE_ARGS+=(
    --failed-run-root "$FAILED_RUN_ROOT"
    --scala-qualification-source "$SCALA_QUALIFICATION_SOURCE"
    --haskell-static-source "$HASKELL_STATIC_SOURCE"
    --haskell-profile-source "$HASKELL_PROFILE_SOURCE"
  )
fi
/usr/bin/python3 "$SUPERVISOR" "${PREPARE_ARGS[@]}"

CONTROL_SUPERVISOR="$RUN_ROOT/control/detached_full_run.py"
[[ -f "$CONTROL_SUPERVISOR" && ! -L "$CONTROL_SUPERVISOR" ]] || {
  echo "sealed control supervisor is unavailable" >&2
  exit 69
}
EXEC_STOP_POST="/usr/bin/python3 $CONTROL_SUPERVISOR service-finalize --run-root $RUN_ROOT"
if ! /usr/bin/systemd-run \
  --user \
  --unit="$UNIT" \
  --description="S1.4X detached full run $RUN_ID" \
  --service-type=exec \
  --working-directory="$ROOT" \
  --expand-environment=no \
  --setenv="HOME=$HOME" \
  --setenv="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib" \
  --setenv="LANG=C.UTF-8" \
  --setenv="LC_ALL=C.UTF-8" \
  --setenv="PYTHONUNBUFFERED=1" \
  --property="Restart=no" \
  --property="KillMode=control-group" \
  --property="KillSignal=SIGTERM" \
  --property="SendSIGKILL=yes" \
  --property="TimeoutStopSec=2min" \
  --property="RuntimeMaxSec=18h" \
  --property="OOMPolicy=stop" \
  --property="StandardInput=null" \
  --property="UMask=0077" \
  --property="StandardOutput=append:$RUN_ROOT/logs/service.stdout.log" \
  --property="StandardError=append:$RUN_ROOT/logs/service.stderr.log" \
  --property="ExecStopPost=$EXEC_STOP_POST" \
  /usr/bin/python3 -u "$CONTROL_SUPERVISOR" run \
    --config "$RUN_ROOT/run-plan.v1.json"
then
  ACTIVE_STATE="$(
    /usr/bin/systemctl --user show "$UNIT" -p ActiveState --value 2>/dev/null \
      || true
  )"
  if [[ "$ACTIVE_STATE" == "active" \
    || "$ACTIVE_STATE" == "activating" \
    || "$ACTIVE_STATE" == "deactivating" ]]; then
    echo "systemd-run returned failure but the exact unit still exists: $ACTIVE_STATE" >&2
    exit 75
  fi
  if [[ ! -e "$RUN_ROOT/terminal/PASS.json" \
    && ! -e "$RUN_ROOT/terminal/FAIL.json" ]]; then
    /usr/bin/python3 "$CONTROL_SUPERVISOR" service-finalize \
      --run-root "$RUN_ROOT" \
      --service-result launch-failed \
      --exit-code exited \
      --exit-status 1 || true
  fi
  exit 1
fi

/usr/bin/sleep 1
ACTIVE_STATE="$(
  /usr/bin/systemctl --user show "$UNIT" -p ActiveState --value
)"
if [[ "$ACTIVE_STATE" != "active" && "$ACTIVE_STATE" != "activating" ]]; then
  /usr/bin/systemctl --user show "$UNIT" \
    -p Id -p LoadState -p ActiveState -p SubState -p Result \
    -p MainPID -p ExecMainCode -p ExecMainStatus -p InvocationID -p NRestarts
  if [[ -f "$RUN_ROOT/terminal/PASS.json" ]]; then
    echo "S1_4X_DETACHED_FULL_RUN_COMPLETED_BEFORE_HANDSHAKE" >&2
  else
    echo "S1_4X_DETACHED_FULL_RUN_DID_NOT_REMAIN_ACTIVE" >&2
    exit 1
  fi
fi

/usr/bin/systemctl --user show "$UNIT" \
  -p Id -p LoadState -p ActiveState -p SubState -p Result \
  -p MainPID -p ExecMainCode -p ExecMainStatus -p InvocationID -p NRestarts
printf 'S1_4X_DETACHED_FULL_RUN_STARTED unit=%s runRoot=%s subject=%s parentRun=%s\n' \
  "$UNIT" "$RUN_ROOT" "$SUBJECT" "$PARENT_RUN_ID"
