#!/bin/bash

set -eu

# if [ "$(id -u)" -eq 0 ]; then
#     su - evert -c "bash $0"
#     exit
# fi

while true; do
    read -p "If you want to setup code-server, enter which port or type N: " response

    if [[ "$response" =~ ^[0-9]+$ ]] && [ "$response" -ge 1 ] && [ "$response" -le 65535 ]; then
        port=$response
        break
    elif [[ "$response" =~ ^n|N ]]; then
        echo "No code-server."
        echo "[]" > /extra_podman_options.json
        exit
    else
        echo "Invalid port number. Please enter a number between 1 and 65535."
    fi
done

# also needs linger enabled for user in systemd
ID=$(id -u evert)
sudo -u evert bash -c "XDG_RUNTIME_DIR=/run/user/$ID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$ID/bus systemctl --user enable code-server"

mkdir -p /home/evert/.config/code-server

cat >/home/evert/.config/code-server/config.yaml <<EOL
bind-addr: 0.0.0.0:$port
auth: none
cert: false
EOL

chown evert:evert /home/evert/.config/code-server/config.yaml

echo "[\"--publish\", \"$port:$port\"]" > /extra_podman_options.json
