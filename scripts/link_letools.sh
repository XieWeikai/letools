#!/usr/bin/env bash
set -euo pipefail

# Expose the repository's locked virtual-environment entry point in the same
# per-user bin directory used by `uv tool install`. This avoids a second runtime
# environment for developers while preserving direct `letools ...` invocation.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd -P)
FORCE=0
SYNC=1
REMOVE=0

usage() {
    cat <<'EOF'
Usage: scripts/link_letools.sh [--force] [--no-sync] [--remove]

  --force    Replace a conflicting user command entry.
  --no-sync  Link the existing .venv without running uv sync --locked.
  --remove   Remove this repository's user command link.
EOF
}

while (($#)); do
    case "$1" in
        --force) FORCE=1 ;;
        --no-sync) SYNC=0 ;;
        --remove) REMOVE=1 ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

BIN_DIR=$(uv tool dir --bin)
LAUNCHER="${BIN_DIR}/letools"
TARGET="${PROJECT_ROOT}/.venv/bin/letools"

if ((REMOVE)); then
    if [[ -L "${LAUNCHER}" && "$(readlink "${LAUNCHER}")" == "${TARGET}" ]]; then
        rm "${LAUNCHER}"
        echo "Removed ${LAUNCHER}"
        exit 0
    fi
    echo "Refusing to remove an entry not owned by this checkout: ${LAUNCHER}" >&2
    exit 1
fi

if ((SYNC)); then
    # The external applications are build inputs. Keep the one-command developer
    # setup contract even when the repository was cloned without recursion.
    git -C "${PROJECT_ROOT}" submodule update --init --recursive
    uv sync --project "${PROJECT_ROOT}" --locked
fi
if [[ ! -x "${TARGET}" ]]; then
    echo "Missing executable after sync: ${TARGET}" >&2
    exit 1
fi

mkdir -p "${BIN_DIR}"
if [[ -e "${LAUNCHER}" || -L "${LAUNCHER}" ]]; then
    if [[ -L "${LAUNCHER}" && "$(readlink "${LAUNCHER}")" == "${TARGET}" ]]; then
        echo "letools command is already linked: ${LAUNCHER}"
        exit 0
    fi
    if ((!FORCE)); then
        echo "Command entry already exists: ${LAUNCHER}" >&2
        echo "Use --force only when it is safe to replace that entry." >&2
        exit 1
    fi
    rm "${LAUNCHER}"
fi
ln -s "${TARGET}" "${LAUNCHER}"
echo "Linked ${LAUNCHER} -> ${TARGET}"

case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
        echo "${BIN_DIR} is not in PATH. Run 'uv tool update-shell', then open a new shell." >&2
        ;;
esac
