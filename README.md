
# probox

**Secure, isolated dev environments made easy**

*⚠️ This project is still a work in progress. I am actually using it in my day-to-day programming, but it will require some work before others can use it too. That being said, it is relatively simple at ~300 lines of Python so feel free to modify and use it.*


## Features

- Easily create rootless podman containers for each project with `probox create`
- Start a shell or run other other commands with `probox run`
- Manage config files between your containers with `probox config push/pull`
- Limit SSH keys access using `probox ssh-add`
- The necessary ports and paths are forwarded in a transparent way, without compromising security
- No dependencies (other than python, podman and ssh-agent)
- Just a thin layer over `podman`


## TODO's

- [ ] Remove hardcoded `evert` usernames, `1000` UID/GIDs, hardcoded `fish` shell, config dir
- [ ] Provide more base images, allow configuring default image
- [ ] Read up on `--userns=keep-id` and `--security-opt label=disable`. Both are needed right now for easy operation but I don't fully understand the security implications.
- [ ] Improve speed of config push/pull, and make it work when container is stopped
- [ ] Make localhost in container reachable on host (now only bindings on 0.0.0.0 are forwarded -- may require a firewall on the host)

Nice to haves:
- [ ] Make config handling use a git repo, with branches per project?
- [ ] Inspect a created container and suggest changes to the original Dockerfile for easier reproducibility (e.g. check installed packages and add to `pacman -S` command in Dockerfile)
- [ ] Provide overview of exposed ports per project
- [ ] Easy inter-container networking?

