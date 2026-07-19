#!/usr/bin/bash

# Aggregate evidence directory는 pinned parent FD 아래에서만 만들고, 공개 route가
# 같은 inode를 계속 가리키는지 각 publish 경계에서 다시 확인한다.

s1_4x_assert_pinned_directory() {
  local route="${1:?directory route is required}"
  local descriptor="${2:?directory descriptor is required}"
  local pinned="/proc/self/fd/$descriptor"
  local route_identity
  local pinned_identity

  if [[ "$route" != /* || -L "$route" || ! -d "$route" || ! -d "$pinned" ]]; then
    echo "pinned directory route changed: $route" >&2
    return 73
  fi
  route_identity="$(/usr/bin/stat -Lc '%d:%i:%f' -- "$route")" || {
    echo "pinned directory route changed: $route" >&2
    return 73
  }
  pinned_identity="$(/usr/bin/stat -Lc '%d:%i:%f' -- "$pinned")" || {
    echo "pinned directory descriptor changed: $route" >&2
    return 73
  }
  if [[ "$route_identity" != "$pinned_identity" \
    || "$(/usr/bin/realpath -e -- "$route")" != "$route" ]]; then
    echo "pinned directory route changed: $route" >&2
    return 73
  fi
}

s1_4x_guarded_command() {
  local route="${1:?directory route is required}"
  local descriptor="${2:?directory descriptor is required}"
  local stdout_path=""
  local discard_stdout="false"
  local command_status
  local close_descriptor
  local -a close_descriptors=()
  shift 2

  while (($# > 0)); do
    case "$1" in
      --close-fd)
        [[ "$#" -ge 2 && "$2" =~ ^[0-9]+$ ]] || {
          echo "guarded command close descriptor is invalid" >&2
          return 64
        }
        close_descriptors+=("$2")
        shift 2
        ;;
      --stdout-path)
        [[ "$#" -ge 2 && -z "$stdout_path" && "$discard_stdout" == "false" ]] || {
          echo "guarded command stdout option is invalid" >&2
          return 64
        }
        stdout_path="$2"
        shift 2
        ;;
      --discard-stdout)
        [[ -z "$stdout_path" && "$discard_stdout" == "false" ]] || {
          echo "guarded command stdout option is invalid" >&2
          return 64
        }
        discard_stdout="true"
        shift
        ;;
      --)
        shift
        break
        ;;
      *)
        echo "guarded command option is invalid: $1" >&2
        return 64
        ;;
    esac
  done
  [[ "$#" -gt 0 ]] || {
    echo "guarded command executable is required" >&2
    return 64
  }
  if [[ -n "$stdout_path" && "$stdout_path" != "$route"/* ]]; then
    echo "guarded command stdout must stay below the pinned route" >&2
    return 64
  fi

  s1_4x_assert_pinned_directory "$route" "$descriptor" || return 73
  # Parent shell keeps authority FDs for the post-check; ordinary producer
  # children receive none of them. A transient route swap inside one long-lived
  # command remains detectable only if it persists through the post-check.
  if (
    for close_descriptor in "${close_descriptors[@]}"; do
      exec {close_descriptor}<&-
    done
    if [[ -n "$stdout_path" ]]; then
      "$@" >"$stdout_path"
    elif [[ "$discard_stdout" == "true" ]]; then
      "$@" >/dev/null
    else
      "$@"
    fi
  ); then
    command_status=0
  else
    command_status=$?
  fi
  s1_4x_assert_pinned_directory "$route" "$descriptor" || return 73
  return "$command_status"
}

s1_4x_pin_existing_directory() {
  local route="${1:?directory route is required}"
  local descriptor_variable="${2:?descriptor variable is required}"
  local descriptor

  if [[ "$route" != /* || -L "$route" || ! -d "$route" ]]; then
    echo "existing output directory identity is unsafe: $route" >&2
    return 73
  fi
  if ! exec {descriptor}<"$route"; then
    echo "existing output directory could not be pinned: $route" >&2
    return 73
  fi
  if ! s1_4x_assert_pinned_directory "$route" "$descriptor"; then
    exec {descriptor}<&-
    return 73
  fi
  printf -v "$descriptor_variable" '%s' "$descriptor"
}

s1_4x_pin_fresh_directory() {
  local route="${1:?fresh directory route is required}"
  local descriptor_variable="${2:?descriptor variable is required}"
  local parent_descriptor_variable="${3:-}"
  local basename_variable="${4:-}"
  local parent
  local basename
  local resolved_parent
  local parent_descriptor
  local descriptor
  local parent_identity
  local pinned_parent_identity
  local parent_mode
  local pinned_route

  if [[ "$route" != /* || "$route" == */ || "$route" == *$'\n'* ]]; then
    echo "fresh output directory route is invalid: $route" >&2
    return 64
  fi
  parent="${route%/*}"
  [[ -n "$parent" ]] || parent="/"
  basename="${route##*/}"
  if [[ -z "$basename" || "$basename" == "." || "$basename" == ".." \
    || "$basename" == *"/"* ]]; then
    echo "fresh output directory basename is invalid: $route" >&2
    return 64
  fi
  resolved_parent="$(/usr/bin/realpath -e -- "$parent")" || {
    echo "canonical output parent is required: $parent" >&2
    return 73
  }
  if [[ "$resolved_parent" != "$parent" || -L "$parent" || ! -d "$parent" ]]; then
    echo "canonical output parent is required: $parent" >&2
    return 73
  fi
  parent_mode="$(/usr/bin/stat -Lc '%a' -- "$parent")" || {
    echo "canonical output parent is required: $parent" >&2
    return 73
  }
  if (( (8#$parent_mode & 0022) != 0 && (8#$parent_mode & 01000) == 0 )); then
    echo "output parent must not be group/world writable without sticky bit: $parent" >&2
    return 73
  fi
  if ! exec {parent_descriptor}<"$parent"; then
    echo "output parent could not be pinned: $parent" >&2
    return 73
  fi
  parent_identity="$(/usr/bin/stat -Lc '%d:%i:%f' -- "$parent")"
  pinned_parent_identity="$(
    /usr/bin/stat -Lc '%d:%i:%f' -- "/proc/self/fd/$parent_descriptor"
  )"
  if [[ "$parent_identity" != "$pinned_parent_identity" ]]; then
    exec {parent_descriptor}<&-
    echo "canonical output parent route changed: $parent" >&2
    return 73
  fi

  pinned_route="/proc/self/fd/$parent_descriptor/$basename"
  if ! (umask 077 && /usr/bin/mkdir -- "$pinned_route"); then
    exec {parent_descriptor}<&-
    echo "fresh output directory already exists or could not be created: $route" >&2
    return 73
  fi
  /usr/bin/chmod 0700 -- "$pinned_route"
  if ! exec {descriptor}<"$pinned_route"; then
    /usr/bin/rmdir -- "$pinned_route" 2>/dev/null || true
    exec {parent_descriptor}<&-
    echo "fresh output directory could not be pinned: $route" >&2
    return 73
  fi
  if ! s1_4x_assert_pinned_directory "$route" "$descriptor"; then
    exec {descriptor}<&-
    /usr/bin/rmdir -- "$pinned_route" 2>/dev/null || true
    exec {parent_descriptor}<&-
    return 73
  fi

  printf -v "$descriptor_variable" '%s' "$descriptor"
  if [[ -n "$parent_descriptor_variable" ]]; then
    printf -v "$parent_descriptor_variable" '%s' "$parent_descriptor"
  fi
  if [[ -n "$basename_variable" ]]; then
    printf -v "$basename_variable" '%s' "$basename"
  fi
}

s1_4x_assert_fresh_child_absent() {
  local parent_descriptor="${1:?parent descriptor is required}"
  local basename="${2:?child basename is required}"
  local route="/proc/self/fd/$parent_descriptor/$basename"

  if [[ "$basename" == "." || "$basename" == ".." || "$basename" == *"/"* \
    || -e "$route" || -L "$route" ]]; then
    echo "fresh output child already exists or is unsafe: $basename" >&2
    return 73
  fi
}
