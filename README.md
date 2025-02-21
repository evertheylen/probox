
# probox

**Secure, isolated dev environments made easy**

*⚠️ This project is still a work in progress. I am actually using it in my day-to-day programming, but it will require some work before others can easily use it too.*

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

- [x] Make it easy to use images with different UIDs/usernames!
- [ ] Read up on podman options regarding security -> [discussion ongoing](https://github.com/containers/podman/discussions/25335)
- [ ] Improve speed of overlay push/pull, and make it work when container is stopped

Nice to haves:
- [ ] Make config handling use a git repo, with branches per project?
- [ ] Inspect a created container and suggest changes to the original Dockerfile for easier reproducibility (e.g. check installed packages and add to `pacman -S` command in Dockerfile)
- [ ] Easy inter-container networking?
- [ ] Only container bindings on 0.0.0.0 are forwarded to host, this will usually require a firewall on the host. Is there a better way?

