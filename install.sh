#!/bin/bash
# Public bootstrap: curl https://raw.githubusercontent.com/geminiwen/ccswap/main/install.sh | bash
# --prefix names the bin directory itself, not its parent. No sudo/package installs.

ccacc_install_main() (
    set -eu
    umask 077

    fail() { printf 'ccacc installer: %s\n' "$*" >&2; exit 1; }
    usage() {
        printf '%s\n' 'Usage: bash install.sh [--prefix /absolute/bin/directory]' \
            'Default: $HOME/.local/bin' \
            'Source overrides: CCACC_INSTALL_REPO=owner/repo CCACC_INSTALL_REF=branch-or-commit'
    }

    prefix="${HOME:-}/.local/bin"
    prefix_seen=
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --prefix)
                [ "$#" -ge 2 ] && [ -n "$2" ] && [ -z "$prefix_seen" ] \
                    || fail '--prefix requires one directory and may appear only once'
                prefix=$2; prefix_seen=yes; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) usage >&2; fail 'unknown argument' ;;
        esac
    done
    [ -n "${HOME:-}" ] || fail 'HOME must name an absolute directory'

    # Reject ambiguous paths rather than resolving through symlinks. This matches
    # the shell hook installer and prevents writes outside the requested tree.
    valid_path() {
        case "$1" in
            /*) ;;
            *) return 1 ;;
        esac
        case "$1/" in
            *//*|*/./*|*/../*|*$'\n'*|*$'\r'*) return 1 ;;
        esac
    }
    # A single trailing slash is harmless and common for a bin directory.
    prefix=${prefix%/}
    valid_path "$prefix" || fail 'prefix must be absolute, without dot components or repeated slashes'
    valid_path "$HOME" || fail 'HOME must be absolute, without dot components or repeated slashes'

    repo=${CCACC_INSTALL_REPO:-geminiwen/ccswap}
    ref=${CCACC_INSTALL_REF:-main}
    case "$repo" in
        */*) owner=${repo%%/*}; name=${repo#*/} ;;
        *) fail 'CCACC_INSTALL_REPO must be owner/repo' ;;
    esac
    for component in "$owner" "$name"; do
        case "$component" in
            ''|.|*[!A-Za-z0-9_.-]*|*..*) fail 'invalid CCACC_INSTALL_REPO' ;;
        esac
    done
    case "$ref" in
        ''|/*|*/|*//*|*..*|./*|*/./*|*/.|.|*[!A-Za-z0-9_./-]*) fail 'invalid CCACC_INSTALL_REF' ;;
    esac

    for tool in bash curl jq mkdir mktemp chmod cp mv rm; do
        command -v "$tool" >/dev/null 2>&1 \
            || fail "missing prerequisite: $tool; install it and rerun this installer"
    done

    # Walk one component at a time, checking before mkdir so existing symlinks
    # (including dangling ones) and non-directory ancestors fail closed.
    ensure_directory() {
        local rest=${1#/} current= part
        while [ -n "$rest" ]; do
            part=${rest%%/*}
            if [ "$rest" = "$part" ]; then rest=; else rest=${rest#*/}; fi
            current=$current/$part
            [ ! -L "$current" ] || fail "symlink directory is not supported: $current"
            if [ -e "$current" ]; then
                [ -d "$current" ] || fail "not a directory: $current"
            else
                mkdir -m 700 -- "$current" || fail "cannot create directory: $current"
            fi
        done
    }
    check_targets() {
        local file
        for file in ccacc ccacc_shell.sh; do
            [ ! -L "$prefix/$file" ] || fail "refusing symlink target: $prefix/$file"
            if [ -e "$prefix/$file" ]; then
                [ -f "$prefix/$file" ] && [ -r "$prefix/$file" ] \
                    || fail "target is not a readable regular file: $prefix/$file"
            fi
        done
    }
    ensure_directory "$prefix"
    [ -w "$prefix" ] || fail "directory is not writable: $prefix"
    check_targets
    stage=$(mktemp -d "$prefix/.ccacc-install.XXXXXXXX") || fail 'cannot create private staging directory'
    retain_stage=
    cleanup() { [ -n "$retain_stage" ] || rm -rf -- "$stage"; }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    chmod 700 "$stage" || fail 'cannot secure staging directory'

    # Resolve mutable names once so a branch update cannot mix file revisions.
    # Full commit IDs already identify immutable content and need no API call.
    commit=$ref
    if ! [[ "$commit" =~ ^[0-9a-fA-F]{40}$ ]]; then
        encoded_ref=$(jq --null-input --raw-output --arg ref "$ref" '$ref | @uri') \
            || fail 'cannot encode CCACC_INSTALL_REF; existing installation was not changed'
        curl --disable --fail --silent --show-error --location --proto '=https' \
            --proto-redir '=https' --output "$stage/commit.json" \
            "https://api.github.com/repos/$repo/commits/$encoded_ref" \
            || fail 'commit lookup failed; existing installation was not changed; rerun the installer'
        # Slurp rejects multiple JSON responses. Validate before command
        # substitution so a SHA containing a trailing newline cannot pass.
        commit=$(jq --slurp --exit-status --raw-output '
            if length == 1 and (.[0] | type == "object") then
                .[0].sha | select(type == "string")
                | select(length == 40 and test("^[0-9a-fA-F]+$"))
            else empty end
        ' "$stage/commit.json") \
            || fail 'invalid commit lookup response; existing installation was not changed'
    fi
    base=https://raw.githubusercontent.com/$repo/$commit
    for file in ccacc ccacc_shell.sh; do
        # Both the original URL and every redirect must stay on HTTPS.
        curl --disable --fail --silent --show-error --location --proto '=https' \
            --proto-redir '=https' --output "$stage/$file" "$base/$file" \
            || fail "download failed for $file; existing installation was not changed; rerun the installer"
        [ -f "$stage/$file" ] && [ ! -L "$stage/$file" ] \
            && [ -r "$stage/$file" ] && [ -s "$stage/$file" ] \
            || fail "empty or unreadable download: $file; existing installation was not changed"
        bash -n "$stage/$file" \
            || fail "invalid Bash download: $file; existing installation was not changed"
    done
    chmod 700 "$stage/ccacc" && chmod 600 "$stage/ccacc_shell.sh" \
        || fail 'cannot set downloaded file permissions; existing installation was not changed'

    # Snapshot the old pair before either rename. Replacing via rename also
    # avoids modifying any other name hard-linked to an old executable.
    check_targets
    for file in ccacc ccacc_shell.sh; do
        if [ -e "$prefix/$file" ]; then
            cp -p -- "$prefix/$file" "$stage/old-$file" \
                || fail 'cannot back up existing installation; existing installation was not changed'
        fi
    done
    rollback() {
        local file failed=
        for file in ccacc ccacc_shell.sh; do
            if [ -f "$stage/old-$file" ]; then
                mv -f -- "$stage/old-$file" "$prefix/$file" || failed=yes
            else
                rm -f -- "$prefix/$file" || failed=yes
            fi
        done
        if [ -n "$failed" ]; then
            retain_stage=yes
            printf 'ccacc installer: rollback failed; the installed pair may be incomplete; backups retained in %s; rerun the installer\n' \
                "$stage" >&2
        fi
    }
    if ! mv -f -- "$stage/ccacc_shell.sh" "$prefix/ccacc_shell.sh" \
        || ! mv -f -- "$stage/ccacc" "$prefix/ccacc"; then
        rollback
        fail 'deployment failed; rerun the installer after fixing the directory permissions'
    fi

    printf 'Installed ccacc and ccacc_shell.sh in %s\n' "$prefix"
    # Never run from staging: the hook records this permanent absolute path.
    # Redirecting stdin also makes curl | bash safe from setup prompts/reads.
    if ! bash "$prefix/ccacc" configure-shell </dev/null; then
        printf 'ccacc installer: files are installed, but shell setup failed. Fix the error and run:\n  bash %q configure-shell\n' \
            "$prefix/ccacc" >&2
        printf '%s\n' 'Or rerun this installer to reinstall both files and retry setup.' >&2
        exit 1
    fi
    printf 'Setup complete. To update this shell, run:\n  source %q\nOr open a new terminal.\n' "$HOME/.ccacc-shell/hook.sh"
    case "$prefix" in
        *:*)
            # Colons separate PATH entries; this directory cannot be one entry.
            printf 'To run ccacc by its absolute path:\n  bash %q\n' "$prefix/ccacc" ;;
        *)
            case ":${PATH:-}:" in
                *:"$prefix":*) ;;
                *) printf 'To add ccacc to this shell PATH, run:\n  export PATH=%q:"$PATH"\n' "$prefix" ;;
            esac ;;
    esac
)

ccacc_install_main "$@"
