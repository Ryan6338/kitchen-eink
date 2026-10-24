#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

install_macos_dependencies() {
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
    echo "Xcode Command Line Tools installation has been opened. Complete it, then rerun this command." >&2
    exit 1
  fi

  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required on macOS: https://brew.sh" >&2
    exit 1
  fi

  brew install cmake ninja pkg-config sdl2 libsodium uv
}

install_debian_dependencies() {
  local -a apt_command

  if (( EUID == 0 )); then
    apt_command=(apt-get)
  elif command -v sudo >/dev/null 2>&1; then
    apt_command=(sudo apt-get)
  else
    echo "Installing simulator packages requires root access or sudo." >&2
    exit 1
  fi

  "${apt_command[@]}" update
  "${apt_command[@]}" install -y \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    libsdl2-dev \
    libsodium-dev \
    ninja-build \
    pkg-config

  if ! command -v uv >/dev/null 2>&1; then
    local uv_installer
    uv_installer="$(mktemp)"
    trap 'rm -f -- "$uv_installer"' EXIT
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$uv_installer"
    sh "$uv_installer"
    rm -f -- "$uv_installer"
    trap - EXIT
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
}

case "$(uname -s)" in
  Darwin)
    install_macos_dependencies
    ;;
  Linux)
    if [[ ! -r /etc/os-release ]]; then
      echo "Cannot identify this Linux distribution; Debian or Ubuntu is required." >&2
      exit 1
    fi

    # shellcheck disable=SC1091
    source /etc/os-release
    if [[ "${ID:-}" != "debian" && "${ID:-}" != "ubuntu" && " ${ID_LIKE:-} " != *" debian "* ]]; then
      echo "Unsupported Linux distribution '${ID:-unknown}'; Debian or Ubuntu is required." >&2
      exit 1
    fi
    install_debian_dependencies
    ;;
  *)
    echo "Unsupported operating system: $(uname -s)" >&2
    exit 1
    ;;
esac

if ! command -v uv >/dev/null 2>&1; then
  echo "uv was installed but is not on PATH. Open a new shell and rerun this command." >&2
  exit 1
fi

uv venv --python 3.12 --clear "$repo_dir/.venv"
uv pip install --python "$repo_dir/.venv/bin/python" \
  --requirement "$repo_dir/requirements-simulator.txt"

"$repo_dir/.venv/bin/esphome" version
echo "Simulator dependencies are ready. Run: just simulator"
