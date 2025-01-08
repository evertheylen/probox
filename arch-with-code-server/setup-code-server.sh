#!/bin/bash

set -eu

if [[ ! $1 =~ ^[0-9]+$ ]]; then
  echo "Error: First argument is not a number." >&2
  exit 1
fi

systemctl enable --user code-server

mkdir -p ~/.config/code-server

cat >~/.config/code-server/config.yaml <<EOL
bind-addr: 0.0.0.0:$1
auth: none
cert: false
EOL
