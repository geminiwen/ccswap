#!/bin/bash
# Companion to ccacc. Source from Bash; generated hooks support Bash and Zsh.
# Public API: ccacc_install_shell_hook ABSOLUTE_CCACC ACCOUNT VALIDATED_TIMEZONE
# Requires probe_alias claude (JSON), valid_timezone ZONE, and jq from the caller.
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
                        next=${input:i:1}; raw=$raw$next
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

ccacc_install_shell_hook() (
    # Subshell contains umask, temporaries and parser variables, not the user's
    # shell. Never source an rc file or evaluate an alias in this process.
    local script=${1:-} account=${2:-} zone=${3:-} private probe original='' missing=0
    local quoted_script rc path tmp stage index=0
    local -a rcs stages _ccacc_raw _ccacc_value
    umask 077
    command -v jq >/dev/null 2>&1 || { _ccacc_shell_error 'jq is required'; return 1; }
    [[ "$account" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && valid_timezone "$zone" || {
        _ccacc_shell_error 'account name and a valid saved account timezone are required'; return 1;
    }
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
    case "${SHELL##*/}" in
        zsh) rcs=("${ZDOTDIR:-$HOME}/.zshrc") ;;
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
        _ccacc_shell_rc "$rc" || { _ccacc_shell_error 'malformed startup file or managed block'; return 1; }
    done
    if [ -f "$private/managed.json" ]; then
        jq -se 'length == 1 and (.[0] | type == "object" and .version == 1 and (.originalMissing | type == "boolean"))' "$private/managed.json" >/dev/null || {
            _ccacc_shell_error 'malformed private hook record'; return 1;
        }
        if jq -se '.[0].originalMissing' "$private/managed.json" >/dev/null; then missing=1
        else
            [ -f "$private/original-alias.json" ] || { _ccacc_shell_error 'original alias backup is missing'; return 1; }
            original=$(cat "$private/original-alias.json") || return 1
        fi
    else
        [ ! -e "$private/original-alias.json" ] && [ ! -e "$private/hook.sh" ] || {
            _ccacc_shell_error 'orphaned private hook files require manual review'; return 1;
        }
        probe=$(probe_alias claude) || { _ccacc_shell_error 'alias probe failed'; return 1; }
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
        probe=$(printf '%s' "$original" | jq -srj '.[0].definition'; printf '.') || return 1
        probe=${probe%.}
        _ccacc_shell_command "$probe" || {
            _ccacc_shell_error 'unsupported Claude alias; use literal assignments, env, or _op_agent env with a claude executable and literal arguments'; return 1;
        }
    fi
    mkdir -p -m 700 "$private" && chmod 700 "$private" || return 1
    tmp=$(mktemp -d "$private/.install.XXXXXXXX") || return 1
    trap 'rm -rf "$tmp"' EXIT
    quoted_script=$(_ccacc_shell_quote "$script")
    cat > "$tmp/hook.sh" <<HOOK
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
    printf '{"version":1,"originalMissing":%s}\n' "$([ "$missing" = 1 ] && printf true || printf false)" > "$tmp/managed.json" || return 1
    if [ "$missing" = 0 ]; then printf '%s\n' "$original" > "$tmp/original-alias.json" || return 1; fi
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
    printf 'Claude shell hook installed for %s (%s).\n' "$account" "$zone"
    printf 'In your current shell, run: source %s\n' "$(_ccacc_shell_quote "$private/hook.sh")"
    printf 'New interactive terminals load it automatically.\n'
)
