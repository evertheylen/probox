
# probox

**Secure, isolated dev environments made easy**

*📢 I currently use this project in my day-to-day programming. If your host is similar to mine (Atomic Fedora) and you don't mind the default container image (Arch Linux with code-server) it should work. Other host/container choices may require some work.*

For some background information, see [the article I wrote about it](https://evertheylen.eu/p/probox-intro/).


## Features

- Easily create rootless podman containers for each project with `probox create`
- Start a shell or run other other commands with `probox run`
- Manage reused files between your containers with `probox overlay push/pull`
- Limit SSH keys access using `probox ssh-add`
- The necessary ports and paths are forwarded in a transparent way, without compromising security
- No dependencies (other than python, podman and ssh-agent)
- Just a thin layer over `podman`
- Podman-in-Podman is supported by default


## Installation

Copy the `probox.py` file to some directory in your $PATH. That's it, there are no dependencies except Python 3.11 or newer.


## TODO's

Big ones:
- [ ] Automatic testing of security through a project like https://github.com/brompwnie/botb
- [ ] Solution for the plethora of developer tools that store symmetric keys/passwords (e.g. `flyctl` or `doctl`)
  Preferably I don't have to MITM every request. I wrote a PoC for `doctl` in `digitalocean_auth.py` that works via OAuth (symmetric password would be stored on the host)
- [ ] Automatic snapshots (on filesystems that support it)
- [ ] Upgrade container without losing settings (`pacman -Syu` in 5 containers will cause them to diverge and no longer share the base image)

Technical:
- [x] Make it easy to use images with different UIDs/usernames!
- [ ] Read up on podman options regarding security -> [discussion ongoing](https://github.com/containers/podman/discussions/25335)
- [ ] Improve speed of overlay push/pull, and make it work when container is stopped

Nice to haves:
- [x] Speed up Podman-in-Podman
- [ ] Make config handling use a git repo, with branches per project?
- [ ] Inspect a created container and suggest changes to the original Dockerfile for easier reproducibility (e.g. check installed packages and add to `pacman -S` command in Dockerfile)
- [ ] Easy inter-container networking?
- [ ] Only container bindings on 0.0.0.0 are forwarded to host, this will usually require a firewall on the host. Is there a better way?

