#!/bin/bash

set -eu

if [[ -f "$HOME/.code-server-setup" ]]; then
    exec fish -l
fi

# Generate a random port
rand_port=$((RANDOM % (65535 - 49152 + 1) + 49152))

while true; do
    read -p "On which port do you want to setup code-server? [$rand_port]/n " response

    if [[ -z "$response" ]]; then
        port=$rand_port
        break
    elif [[ "$response" =~ ^[0-9]+$ ]] && [ "$response" -ge 1 ] && [ "$response" -le 65535 ]; then
        port=$response
        break
    elif [[ "$response" =~ ^n|N ]]; then
        echo "No code-server."
        echo 'no' > "$HOME/.code-server-setup"
        exec fish -l
    else
        echo "Invalid port number. Please enter a number between 1 and 65535."
    fi
done

# also needs linger enabled for user in systemd
echo 'yes' > "$HOME/.code-server-setup"
systemctl --user enable --now code-server

mkdir -p ~/.config/code-server

# ugly hack follows ugly hack: config.yaml lets you specify any CLI argument of code-server
# But how do you specify positional arguments then? Reading the code, they use '_' as the key
# for positional arguments. See https://github.com/coder/code-server/blob/main/src/node/cli.ts
cat >~/.config/code-server/config.yaml <<EOL
app-name: "$HOSTNAME 📦"
bind-addr: 0.0.0.0:$port
auth: none
cert: false
_: "$PROJECT_PATH"
EOL

mkdir -p ~/.config/fish/conf.d/
cat >~/.config/fish/conf.d/01-greet-code-server.fish <<EOL
function fish_greeting
    echo Welcome to fish! code-server is running on http://127.0.0.1:$port
end
EOL

exec fish -l
