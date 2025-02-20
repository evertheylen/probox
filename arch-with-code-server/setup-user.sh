#!/bin/bash

set -e

groupadd -g "$GID" "$USER" && useradd -m -u "$UID" -g "$GID" -s /bin/fish "$USER"
echo "$USER ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

echo "$USER:10000:5000" > /etc/subuid
echo "$USER:10000:5000" > /etc/subgid

touch "/var/lib/systemd/linger/$USER"

sudo -i -u "$USER" bash -e << ENDSCRIPT

code-server --install-extension ms-python.python
code-server --install-extension llvm-vs-code-extensions.vscode-clangd
code-server --install-extension rust-lang.rust-analyzer
code-server --install-extension eamodio.gitlens
code-server --install-extension ms-toolsai.jupyter
code-server --install-extension golang.go
code-server --install-extension timonwong.shellcheck
code-server --install-extension github.github-vscode-theme
#code-server --install-extension alefragnani.bookmarks

# Again, for rootless PINP
mkdir -p ~/.config/containers
cat <<EOF >~/.config/containers/containers.conf
[containers]
volumes = [
	"/proc:/proc",
]
default_sysctls = []
EOF

mkdir -p ~/.config/fish/

cat <<EOF > ~/.config/fish/config.fish
set -x XDG_RUNTIME_DIR /run/user/$UID
set -x DBUS_SESSION_BUS_ADDRESS unix:path=/run/user/$UID/bus
set -x SSH_AUTH_SOCK /home/$USER/ssh-agent.socket
set -x GIT_EDITOR nano
set -x EDITOR nano
EOF

ENDSCRIPT

