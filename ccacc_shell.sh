#!/bin/bash
# Companion to ccacc. Source from Bash; generated hooks support Bash and Zsh.
# Public API: ccacc_install_shell_hook / ccacc_check_shell_hook
# Arguments: ABSOLUTE_CCACC
# Requires probe_alias claude (JSON) and jq from the caller.
# Captured aliases are parsed as literal simple commands, never evaluated.

_ccacc_shell_error() { printf 'ccacc shell hook: %s\n' "$*" >&2; }

_ccacc_shell_quote() {
    local value=$1
    value=${value//\'/\'\\\'\'}
    printf "'%s'" "$value"
}

# Refuse links at every path component, nonregular targets, and path traversal.
_ccacc_shell_path() {
    local path=$1 kind=$2 part current='' rest
    case "$path" in /*) ;; *) return 1 ;; esac
    case "$path/" in *'/../'*|*'/./'*|*'//'*) return 1 ;; esac
    rest=${path#/}
    while [ -n "$rest" ]; do
        part=${rest%%/*}
        current="$current/$part"
        [ ! -L "$current" ] || return 1
        if [ "$rest" = "$part" ]; then
            if [ -e "$current" ]; then
                if [ "$kind" = dir ]; then [ -d "$current" ] || return 1
                else [ -f "$current" ] || return 1; fi
            fi
            break
        fi
        [ ! -e "$current" ] || [ -d "$current" ] || return 1
        rest=${rest#*/}
    done
}

# A deliberately small, non-executing shell lexer. Decode literal quoting to
# identify assignment names and command boundaries, keeping each original word.
# Expansions, operators, comments and continuations are unsupported: accepting
# those without a complete shell parser could change behavior or run code.
_ccacc_shell_words() {
    local input=$1 i char next state=plain raw='' value='' started=0
    _ccacc_raw=() _ccacc_value=()
    for ((i=0; i<${#input}; i++)); do
        char=${input:i:1}
        case "$state" in
            single)
                raw=$raw$char
                if [ "$char" = "'" ]; then state=plain; else value=$value$char; fi
                ;;
            double)
                raw=$raw$char
                case "$char" in
                    '"') state=plain ;;
                    '$'|'`') return 1 ;;
                    '\')
                        i=$((i+1)); [ "$i" -lt "${#input}" ] || return 1
                        next=${input:i:1}; [ "$next" != $'\n' ] || return 1
                        raw=$raw$next
                        case "$next" in '$'|'`'|'"'|'\') value=$value$next ;; *) value=$value\\$next ;; esac
                        ;;
                    *) value=$value$char ;;
                esac
                ;;
            plain)
                case "$char" in
                    ' '|$'\t')
                        if [ "$started" = 1 ]; then
                            _ccacc_raw+=("$raw"); _ccacc_value+=("$value")
                            raw='' value='' started=0
                        fi
                        ;;
                    "'") state=single; raw=$raw$char; started=1 ;;
                    '"') state=double; raw=$raw$char; started=1 ;;
                    '\')
                        i=$((i+1)); [ "$i" -lt "${#input}" ] || return 1
                        next=${input:i:1}; [ "$next" != $'\n' ] || return 1
                        raw=$raw$char$next; value=$value$next; started=1
                        ;;
                    '$'|'`'|';'|'&'|'|'|'<'|'>'|'('|')'|'{'|'}'|'#'|'~'|'*'|'?'|'['|']'|$'\n'|$'\r') return 1 ;;
                    *) raw=$raw$char; value=$value$char; started=1 ;;
                esac
                ;;
        esac
    done
    [ "$state" = plain ] || return 1
    if [ "$started" = 1 ]; then _ccacc_raw+=("$raw"); _ccacc_value+=("$value"); fi
    [ "${#_ccacc_raw[@]}" -gt 0 ]
}

# Retain literal leading assignments and the supported env / _op_agent env
# prefix. Only BROWSER and TZ assignments are removed. Unknown wrappers fail
# before any files change, rather than losing wrapper options or arguments.
_ccacc_shell_command() {
    local definition=$1 i=0 token name base prefix='' assignments=''
    _ccacc_shell_words "$definition" || return 1
    while [ "$i" -lt "${#_ccacc_raw[@]}" ]; do
        token=${_ccacc_value[$i]}
        if [[ "$token" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            name=${token%%=*}
            case "${_ccacc_raw[$i]}" in "$name="*) ;; *) return 1 ;; esac
            case "$name" in BROWSER|TZ) ;; *) assignments="$assignments ${_ccacc_raw[$i]}" ;; esac
            i=$((i+1))
        else break; fi
    done
    [ "$i" -lt "${#_ccacc_raw[@]}" ] || return 1
    token=${_ccacc_value[$i]}; base=${token##*/}
    if [ "$base" = _op_agent ]; then
        prefix=$(_ccacc_shell_quote "$token"); i=$((i+1))
        [ "${_ccacc_value[$i]:-}" = env ] || return 1
        prefix="$prefix 'env'"; i=$((i+1))
    elif [ "$base" = env ]; then
        prefix=$(_ccacc_shell_quote "$token"); i=$((i+1))
    fi
    if [ -n "$prefix" ]; then
        # Keep the first assignment group before the wrapper, and the second
        # after env; their order can matter to credential wrappers.
        _ccacc_command="$assignments BROWSER=false TZ=\"\$__ccacc_tz\" $prefix"
        assignments=''
        while [ "$i" -lt "${#_ccacc_raw[@]}" ]; do
            token=${_ccacc_value[$i]}
            if [[ "$token" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
                name=${token%%=*}
                case "${_ccacc_raw[$i]}" in "$name="*) ;; *) return 1 ;; esac
                case "$name" in BROWSER|TZ) ;; *) assignments="$assignments ${_ccacc_raw[$i]}" ;; esac
                i=$((i+1))
            else break; fi
        done
    else _ccacc_command=''; fi
    [ "$i" -lt "${#_ccacc_raw[@]}" ] || return 1
    token=${_ccacc_value[$i]}; base=${token##*/}
    [ "$base" = claude ] || return 1
    _ccacc_command="$_ccacc_command$assignments BROWSER=false TZ=\"\$__ccacc_tz\""
    # Quote decoded arguments to prevent source-time alias expansion, globbing,
    # and assignment interpretation in a different shell.
    while [ "$i" -lt "${#_ccacc_raw[@]}" ]; do
        _ccacc_command="$_ccacc_command $(_ccacc_shell_quote "${_ccacc_value[$i]}")"
        i=$((i+1))
    done
    _ccacc_command="$_ccacc_command \"\$@\""
}

# Read every byte, including trailing newlines; reject NUL instead of silently
# dropping it in a shell variable. rc text outside our block stays byte-for-byte.
_ccacc_shell_read() {
    local path=$1 content
    if [ ! -e "$path" ]; then _ccacc_content=''; return 0; fi
    [ -r "$path" ] || return 1
    # Bash read -d '' stops at a NUL; success therefore means malformed input.
    if IFS= read -r -d '' content < "$path"; then return 1; fi
    _ccacc_content=$content
}

_ccacc_shell_rc() {
    local path=$1 begin=$'\n# >>> ccacc shell hook >>>\n' end=$'# <<< ccacc shell hook <<<\n'
    local before after middle
    _ccacc_shell_read "$path" || return 1
    case "$_ccacc_content" in
        *'# >>> ccacc shell hook >>>'*|*'# <<< ccacc shell hook <<<'*)
            case "$_ccacc_content" in *"$begin"*"$end"*) ;; *) return 1 ;; esac
            before=${_ccacc_content%%"$begin"*}
            middle=${_ccacc_content#*"$begin"}
            after=${middle#*"$end"}
            middle=${middle%%"$end"*}
            case "$before$middle$after" in *'# >>> ccacc shell hook >>>'*|*'# <<< ccacc shell hook <<<'*) return 1 ;; esac
            # Only replace a block whose body is our one source command.
            case "$middle" in '. '*$'\n') ;; *) return 1 ;; esac
            case "${middle%$'\n'}" in *$'\n'*) return 1 ;; esac
            # A newline added before the original block may now separate a
            # formerly unterminated last line from text appended afterwards.
            if [ -n "$before" ] && [ -n "$after" ]; then
                case "$before" in *$'\n') ;; *)
                    case "$after" in $'\n'*) ;; *) before=$before$'\n' ;; esac
                    ;;
                esac
            fi
            _ccacc_content=$before$after
            ;;
    esac
    _ccacc_content="$_ccacc_content$begin. $(_ccacc_shell_quote "$private/hook.sh")
$end"
}

_ccacc_shell_replace() {
    # Preserve mtimes and inodes when generated bytes have not changed.
    if [ -f "$2" ] && cmp -s "$1" "$2"; then return 0; fi
    mv -f "$1" "$2"
}

_ccacc_shell_hook_text() {
    quoted_script=$(_ccacc_shell_quote "$script")
    cat <<HOOK
# Managed by ccacc. Account timezone is resolved on every launch.
unalias claude 2>/dev/null || :
function __ccacc_launch { (
    __ccacc_tz=\$(command bash $quoted_script timezone) || {
        printf '%s\\n' 'ccacc: cannot read the active account timezone; configure it before launching Claude' >&2
        return 1
    }
    case "\$__ccacc_tz" in
        ''|/*|*..*|*[!A-Za-z0-9_+/-]*)
            printf '%s\\n' 'ccacc: invalid or missing active account timezone' >&2; return 1 ;;
    esac
    if [ ! -f "/usr/share/zoneinfo/\$__ccacc_tz" ] && [ ! -f "/var/db/timezone/zoneinfo/\$__ccacc_tz" ]; then
        printf '%s\\n' 'ccacc: unknown active account timezone' >&2; return 1
    fi
    $_ccacc_command
) }
alias claude='__ccacc_launch'
HOOK
}

_ccacc_shell_probe_context() {
    if [ "$check_only" = --installed ] && [ "${SHELL##*/}" = bash ]; then
        probe='{"missing":true}'
    else
        probe=$(probe_alias claude) || { _ccacc_shell_error 'alias probe failed'; return 1; }
    fi
    printf '%s' "$probe" | jq -se 'length == 1 and (.[0] | type == "object" and (
        (.missing == true and (has("definition") | not)) or
        ((has("missing") | not) and (.definition | type == "string") and
         (.definition | length > 0) and ((.definition | contains("\u0000")) | not))))' >/dev/null || {
        _ccacc_shell_error 'malformed alias probe'; return 1;
    }
}

_ccacc_shell_check_text() {
    [ -f "$private/hook.sh" ] && _ccacc_shell_read "$private/hook.sh" || {
        _ccacc_shell_error 'installed hook is missing or unreadable'; return 1;
    }
    expected=$(_ccacc_shell_hook_text; printf '.')
    [ "$_ccacc_content." = "$expected" ] || { _ccacc_shell_error 'installed hook does not match its records'; return 1; }
}

_ccacc_shell_hook() (
    # Subshell contains umask, temporaries and parser variables, not the user's
    # shell. Never source an rc file or evaluate an alias in this process.
    local script=${1:-} check_only=${2:-} private probe definition original='' missing=0 expected actual record
    local quoted_script rc path tmp stage startup_directory index=0
    local -a rcs stages _ccacc_raw _ccacc_value
    umask 077
    [ "$#" -le 2 ] && { [ -z "$check_only" ] || [ "$check_only" = --validate-only ] || [ "$check_only" = --installed ]; } || return 1
    command -v jq >/dev/null 2>&1 || { _ccacc_shell_error 'jq is required'; return 1; }
    _ccacc_shell_path "$script" file && [ -f "$script" ] || {
        _ccacc_shell_error 'ccacc must be an absolute regular file with no symlink components'; return 1;
    }
    _ccacc_shell_path "${HOME:-}" dir && [ -d "$HOME" ] || {
        _ccacc_shell_error 'HOME must be an absolute directory with no symlink components'; return 1;
    }
    private="$HOME/.ccacc-shell"
    _ccacc_shell_path "$private" dir || { _ccacc_shell_error 'unsafe private hook directory'; return 1; }
    for path in "$private/managed.json" "$private/original-alias.json" "$private/hook.sh"; do
        _ccacc_shell_path "$path" file || { _ccacc_shell_error 'unsafe private hook target'; return 1; }
    done
    if [ ! -f "$private/managed.json" ] && [ "$check_only" != --installed ]; then
        [ ! -e "$private/original-alias.json" ] && [ ! -e "$private/hook.sh" ] || {
            _ccacc_shell_error 'orphaned private hook files require manual review'; return 1;
        }
        _ccacc_shell_probe_context || return 1
    fi
    if [ -f "$private/managed.json" ]; then
        jq -se 'length == 1 and (.[0] | type == "object" and
            ((.version == 1 and keys == ["originalMissing", "version"]) or .version == 2) and
            (.originalMissing | type == "boolean"))' "$private/managed.json" >/dev/null || {
            _ccacc_shell_error 'malformed private hook record'; return 1;
        }
        if jq -se '.[0].originalMissing' "$private/managed.json" >/dev/null; then missing=1
        else
            [ -f "$private/original-alias.json" ] || { _ccacc_shell_error 'original alias backup is missing'; return 1; }
            _ccacc_shell_read "$private/original-alias.json" || {
                _ccacc_shell_error 'unreadable or malformed original alias backup'; return 1;
            }
            original=$_ccacc_content
        fi
    else
        [ "$check_only" != --installed ] || { _ccacc_shell_error 'installation record is missing'; return 1; }
        [ ! -e "$private/original-alias.json" ] && [ ! -e "$private/hook.sh" ] || {
            _ccacc_shell_error 'orphaned private hook files require manual review'; return 1;
        }
        if printf '%s' "$probe" | jq -se 'length == 1 and (.[0] | type == "object" and .missing == true and (has("definition") | not))' >/dev/null; then missing=1
        else original=$probe; fi
    fi
    if [ "$missing" = 1 ]; then
        [ ! -e "$private/original-alias.json" ] || { _ccacc_shell_error 'unexpected original alias backup'; return 1; }
        _ccacc_shell_command claude || return 1
    else
        printf '%s' "$original" | jq -se 'length == 1 and (.[0] | type == "object" and (.definition | type == "string") and (.definition | length > 0) and ((.definition | contains("\u0000")) | not))' >/dev/null || {
            _ccacc_shell_error 'malformed alias probe or backup'; return 1;
        }
        # Sentinel preserves trailing newlines so malformed commands cannot be
        # accepted accidentally after command substitution strips them.
        definition=$(printf '%s' "$original" | jq -srj '.[0].definition'; printf '.') || return 1
        definition=${definition%.}
        _ccacc_shell_command "$definition" || {
            _ccacc_shell_error 'unsupported Claude alias; use literal assignments, env, or _op_agent env with a claude executable and literal arguments'; return 1;
        }
    fi
    if [ "$check_only" = --installed ] || [ -f "$private/hook.sh" ]; then
        if ! _ccacc_shell_check_text; then
            _ccacc_shell_error "inspect and move aside the unverified $private/hook.sh manually, then run ccacc install"
            return 1
        fi
    fi
    # Existing Zsh hooks are verified before startup context can source them.
    if [ -f "$private/managed.json" ]; then
        _ccacc_shell_probe_context || return 1
    fi
    case "${SHELL##*/}" in
        zsh)
            if printf '%s' "$probe" | jq -se '.[0] | has("startupDirectory")' >/dev/null; then
                printf '%s' "$probe" | jq -se '.[0].startupDirectory | select(type == "string")
                    | length > 0 and (contains("\u0000") | not) and
                      (contains("\n") | not) and (contains("\r") | not)' >/dev/null || {
                    _ccacc_shell_error 'malformed probed Zsh startup directory'; return 1;
                }
                startup_directory=$(printf '%s' "$probe" | jq -srj '.[0].startupDirectory') || return 1
            else startup_directory=${ZDOTDIR:-$HOME}; fi
            case "$startup_directory" in *$'\n'*|*$'\r'*)
                _ccacc_shell_error 'malformed Zsh startup directory'; return 1 ;;
            esac
            _ccacc_shell_path "$startup_directory" dir && [ -d "$startup_directory" ] || {
                _ccacc_shell_error 'Zsh startup directory is unsafe or missing'; return 1;
            }
            # .zlogin runs after .zshrc in interactive login shells, so both
            # files need the hook at the end to override earlier fixed aliases.
            rcs=("$startup_directory/.zshrc" "$startup_directory/.zlogin")
            ;;
        bash)
            rcs=("$HOME/.bashrc")
            # Bash reads the first existing login file, even if it is empty.
            rc="$HOME/.bash_profile"
            for path in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
                _ccacc_shell_path "$path" file || { _ccacc_shell_error 'unsafe Bash login startup target'; return 1; }
                if [ -e "$path" ]; then rc=$path; break; fi
            done
            rcs+=("$rc")
            ;;
        *) _ccacc_shell_error 'only Bash and Zsh startup files are supported'; return 1 ;;
    esac
    for rc in "${rcs[@]}"; do
        _ccacc_shell_path "$rc" file && [ -d "${rc%/*}" ] || {
            _ccacc_shell_error 'startup file or its parent is unsafe or missing'; return 1;
        }
        if [ "$check_only" != --installed ]; then
            [ -w "${rc%/*}" ] && { [ ! -e "$rc" ] || [ -w "$rc" ]; } || {
                _ccacc_shell_error 'startup file and its parent must be writable'; return 1;
            }
        fi
        _ccacc_shell_rc "$rc" || { _ccacc_shell_error 'malformed startup file or managed block'; return 1; }
    done
    # Version 2 binds the record to this deployment and actual startup paths.
    # Version 1 is recognized by exact hook/source bytes and migrated only on install.
    record=$(jq -cn --arg script "$script" --arg shell "${SHELL##*/}" \
        --arg first "${rcs[0]}" --arg second "${rcs[1]}" --argjson missing "$([ "$missing" = 1 ] && printf true || printf false)" \
        '{version:2,originalMissing:$missing,script:$script,shell:$shell,startupFiles:[$first,$second]}') || return 1
    if [ "$check_only" = --installed ]; then
        if jq -e '.version == 2' "$private/managed.json" >/dev/null; then
            printf '%s' "$record" | jq -e --slurpfile saved "$private/managed.json" '. == $saved[0]' >/dev/null || {
                _ccacc_shell_error 'installation record does not match this script or startup paths'; return 1;
            }
        fi
        for rc in "${rcs[@]}"; do
            [ -f "$rc" ] && _ccacc_shell_read "$rc" || return 1
            actual=$_ccacc_content
            _ccacc_shell_rc "$rc" || return 1
            [ "$actual" = "$_ccacc_content" ] || {
                _ccacc_shell_error 'required managed source entry is missing, stale or misplaced'; return 1;
            }
        done
        return 0
    fi
    [ "$check_only" != --validate-only ] || return 0
    mkdir -p -m 700 "$private" && chmod 700 "$private" || return 1
    tmp=$(mktemp -d "$private/.install.XXXXXXXX") || return 1
    trap 'rm -rf "$tmp"' EXIT
    _ccacc_shell_hook_text > "$tmp/hook.sh" || return 1
    printf '%s\n' "$record" > "$tmp/managed.json" || return 1
    if [ "$missing" = 0 ]; then
        if [ -f "$private/original-alias.json" ]; then
            printf '%s' "$original" > "$tmp/original-alias.json" || return 1
        else
            printf '%s\n' "$original" > "$tmp/original-alias.json" || return 1
        fi
    fi
    for rc in "${rcs[@]}"; do
        _ccacc_shell_rc "$rc" || return 1
        stage="$tmp/rc.$index"
        if [ -e "$rc" ]; then cp -p "$rc" "$stage" || return 1; fi
        printf '%s' "$_ccacc_content" > "$stage" || return 1
        stages+=("$stage"); index=$((index+1))
    done
    chmod 600 "$tmp/hook.sh" "$tmp/managed.json" || return 1
    if [ "$missing" = 0 ]; then
        chmod 600 "$tmp/original-alias.json" || return 1
        _ccacc_shell_replace "$tmp/original-alias.json" "$private/original-alias.json" || return 1
    fi
    _ccacc_shell_replace "$tmp/hook.sh" "$private/hook.sh" && _ccacc_shell_replace "$tmp/managed.json" "$private/managed.json" || return 1
    chmod 600 "$private/hook.sh" "$private/managed.json" || return 1
    if [ "$missing" = 0 ]; then chmod 600 "$private/original-alias.json" || return 1; fi
    index=0
    for rc in "${rcs[@]}"; do
        _ccacc_shell_replace "${stages[$index]}" "$rc" || return 1
        index=$((index+1))
    done
    printf 'Claude shell hook installed.\n'
    printf 'In your current shell, run: source %s\n' "$(_ccacc_shell_quote "$private/hook.sh")"
    printf 'New interactive terminals load it automatically.\n'
)

ccacc_install_shell_hook() { _ccacc_shell_hook "$@"; }
ccacc_check_shell_hook() { _ccacc_shell_hook "$1" --installed; }
# Compatibility for explicit installer preflight callers; never an installed check.
ccacc_preflight_shell_hook() { _ccacc_shell_hook "$1" --validate-only; }
