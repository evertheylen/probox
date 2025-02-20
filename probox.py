#!/usr/bin/env python3

import argparse, sys, os, signal, json, subprocess, tempfile, socket, random, getpass, grp, tomllib
from pathlib import Path

# TODO: automatic error handling?

# Problem:
#   Failed to create control group inotify object: Too many open files
#   Failed to allocate manager object: Too many open files
# Solution: sudo sysctl fs.inotify.max_user_instances=8192

# Problem (during building):
#   error while loading shared libraries: libc.so.6: cannot change memory protections
# Solution: restorecon -RFv $HOME/.local/share/containers


START = '\033[1;33m>>>'
END = '\033[0m\n'
GENERIC_NAMES = {'src', 'source', 'project', 'dir', 'folder', 'git', 'repo', 'repository', 'code'}

default_config_file = """
default_image = "docker.io/evertheylen/arch-with-code-server"
# All contents of this directory will be pushed into the home directory of the container
#home_overlay = "/home/foobar/configs/"
"""


config = None


def status(*text):
    print(START, *text, end=END, file=sys.stderr)


def capture_podman(*args, format_json=True):
    res = subprocess.run(['podman', *args, *(['--format', 'json'] if format_json else [])], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def run_podman(*args, check=True, quiet=False, **kwargs):
    command = ['podman', *args]
    status(' '.join(command))
    if quiet:
        kwargs['stdout'] = subprocess.DEVNULL
    return subprocess.run(command, check=check, text=True, **kwargs)


def get_containers():
    containers = capture_podman('container', 'ls', '--all', '--filter', 'label=probox.project_path')
    containers_by_path = {Path(c['Labels']['probox.project_path']): c for c in containers}
    containers_by_name = {c['Names'][0]: c for c in containers}
    return containers_by_path, containers_by_name


def suggest_name(path, _taken):
    taken = set(_taken) | GENERIC_NAMES

    # First try: pick the name of the folder we're in
    abspath = path.absolute()
    if abspath.name not in taken:
        return path.name

    # Second try: use the parent name + current dir (e.g. if we're making a lot of 'src' dirs)
    if abspath.parent.name != '':
        combo = abspath.parent.name + '-' + abspath.name
        if combo not in taken:
            return combo

    # Third try: use a number
    i = 2
    while True:
        name_and_number = abspath.name + f"-{i:>03}"
        if name_and_number not in taken:
            return name_and_number
        i += 1


def ssh_agent_socket(name):
    return f'/run/user/{os.getuid()}/{name}-ssh.sock'


def ssh_agent_pid(name):
    processes = subprocess.run(['pgrep', '-f', f'ssh-agent -a {ssh_agent_socket(name)}'], capture_output=True, text=True)
    pid_str = processes.stdout.strip()
    if pid_str != '':
        return int(pid_str)
    else:
        return None


def start_ssh_agent(name):
    pid = ssh_agent_pid(name)
    if pid is None:
        status("Starting ssh-agent")
        subprocess.run(['ssh-agent', '-a', ssh_agent_socket(name)], stdout=subprocess.DEVNULL, check=True)


def stop_ssh_agent(name):
    pid = ssh_agent_pid(name)
    if pid is not None:
        os.kill(pid, signal.SIGTERM)
    else:
        status("No ssh-agent found")


def parse_user_triple(triple_str):
    username, uid_s, gid_s = triple_str.split(':')
    return (username, int(uid_s), int(gid_s))


def stringify_user_triple(username, uid, gid):
    return f"{username}:{uid}:{gid}"


def image_with_user(from_image, username, uid, gid):
    # Builds an image from a certain base image
    base_images = capture_podman('image', 'ls', '--all', from_image)
    if len(base_images) == 0:
        status(f"No base image found for {from_image}, pulling...")
        run_podman('pull', from_image, check=True)
        base_images = capture_podman('image', 'ls', '--all', from_image)

    if len(base_images) != 1:
        status(f"Found {len(base_images)} matching '{from_image}', expecting exactly 1")
        sys.exit(1)

    parent_image = base_images[0]["Id"]
    images = capture_podman('image', 'ls', '--all', '--filter', f'label=probox.parent_image={parent_image}')
    images_by_user_triple = {parse_user_triple(i['Labels']['probox.user_triple']): i for i in images}

    existing_image = images_by_user_triple.get((username, uid, gid))

    if existing_image is None:
        image_data = capture_podman('image', 'inspect', parent_image)[0]
        setup_user_cmd = image_data["Config"]["Labels"].get("probox.setup_user")

        if setup_user_cmd is None:
            status("No probox.setup_user script specified!")
            sys.exit(1)

        container_id = run_podman(
            'create', '--tz=local', '--rm',
            '--label', f'probox.parent_image={parent_image}',
            '--label', f'probox.user_triple={stringify_user_triple(username, uid, gid)}',
            parent_image, capture_output=True
        ).stdout.strip()

        try:
            run_podman('start', container_id, quiet=True)
            run_podman('exec', '--env', f'USER={username}', '--env', f'UID={uid}', '--env', f'GID={gid}', container_id, setup_user_cmd, check=True)
            image_id = run_podman('commit', container_id, '--pause=true', capture_output=True).stdout.strip()
            status(f"Made new image for {username} (UID={uid}, GID={gid}) based on {from_image} -> {image_id}")
            return image_id
        finally:
            run_podman('stop', container_id, quiet=True)
    else:
        image_id = existing_image["Id"]
        status(f"Found existing image for {username} (UID={uid}, GID={gid}) based on {from_image} -> {image_id}")
        return image_id


def create(*, path=None, name=None, from_image=None, privileged=False, push_overlay=True, ignore_post_create_cmd=False, ignore_existing_containers=False):
    containers_by_path, containers_by_name = get_containers()
    proj_path = Path(os.getcwd() if path is None else path).absolute()
    if not ignore_existing_containers and proj_path in containers_by_path:
        status("Path already registered!", containers_by_path[proj_path]['Names'][0])
        sys.exit(1)

    name = name or suggest_name(proj_path, containers_by_name.keys())
    if name is None:
        status("Couldn't determine a name from the path. Specify a name with --name.")
        sys.exit(1)
    if '.' in name or '/' in name:
        status("Name can't contain . or /")
        sys.exit(1)

    basic_create_options = ['--name', name, '--hostname', name, '--tz=local']

    if from_image is None:
        from_image = config['default_image']

    image_id = image_with_user(from_image, getpass.getuser(), os.getuid(), os.getgid())

    # Maybe look at https://github.com/containers/podman/discussions/13728#discussioncomment-2900471 ?
    # In particular, this comment says something like using --userns=auto "with a huge /etc/subuid range"
    # Already did the subuid thing via
    #   sudo usermod --add-subuids 1000000-990000000 --add-subgids 1000000-990000000 evert
    # But then mapping the volume is impossible (I'd use `:idmap=uids=1000-1000-1;gids=1000-1000-1`), see https://github.com/containers/crun/issues/1632

    start_ssh_agent(name)

    if not privileged and Path.home() != proj_path:
        # see https://github.com/containers/podman/discussions/25335#discussioncomment-12237404
        proj_dir_mount_opts = ['--volume', f'{proj_path}:{proj_path}:Z']
    else:
        # fallback to not kill a users home directory (docs require us to do it)
        proj_dir_mount_opts = ['--volume', f'{proj_path}:{proj_path}']
        if not privileged:
            status("WARNING: home dir is selected as main directory, disabling SELinux!")
            proj_dir_mount_opts.extend(['--security-opt', 'label=disable'])

    run_podman(
        'create', *basic_create_options, '--label', f'probox.project_path={proj_path}',
        '--userns=keep-id',
        '--pids-limit=-1',
        '--cap-add=NET_RAW',  # For pings as non-root
        '--device=/dev/fuse',  # For rootless PINP, see https://www.redhat.com/en/blog/podman-inside-container
        *proj_dir_mount_opts,
        *(['--privileged'] if privileged else []),
        '--volume', f"{ssh_agent_socket(name)}:{Path.home() / 'ssh-agent.sock'}:Z",  # also with :Z flag
        # pasta: auto forward ports from container to host, but not other way around
        # WARNING: binding on 0.0.0.0 in a container will ALSO expose it on 0.0.0.0 on the host!
        # I use a firewall to fix this, so I can also temporarily allow it (e.g. to allow my phone on WiFi to view a webapp)
        '--network=pasta:-t,auto,-u,auto,-T,none,-U,none',
        image_id
    )

    if push_overlay:
       run_podman('start', name, quiet=True)
       push_overlay_to_container(name)


def find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name):
    if path_or_name is None or '/' in path_or_name or '.' in path_or_name:
        deep_path = Path(path_or_name or os.getcwd()).absolute()
        for path in [deep_path] + list(deep_path.parents):
            con = containers_by_path.get(path)
            if con is not None:
                return con["Names"][0]
        status("No container found for directory", deep_path, 'in', containers_by_path.keys())
        sys.exit(1)

    if path_or_name not in containers_by_name:
        status(f"Couldn't find name '{path_or_name}'")
        sys.exit(1)

    return path_or_name


def run(*, path_or_name, cmd=None):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    project_path = Path(container_data['Config']['Labels']['probox.project_path'])

    if not container_data['State']['Running']:
        start_ssh_agent(container_name)
        run_podman('start', container_name, quiet=True)

    cwd = Path(os.getcwd()).absolute()
    if project_path == cwd or project_path in cwd.parents:
        workdir = cwd
    else:
        workdir = Path.home()

    if not cmd:
        cmd = container_data['Config']['Labels'].get('probox.start_shell', '/bin/bash').split(' ')

    env = {
        'SSH_AUTH_SOCK': str(Path.home() / 'ssh-agent.sock'),
        # Assume linger in systemd
        'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{os.getuid()}/bus',
        'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
        'PWD': workdir,
        'PROJECT_PATH': project_path
    }

    with tempfile.NamedTemporaryFile(mode='w+') as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")
        f.flush()
        run_podman('exec', '-it', '--user', getpass.getuser(), '--workdir', str(workdir), '--env-file', f.name, container_name, *cmd, check=False)


def temp(path=None, from_image=None, privileged=False, push_overlay=True):
    random_id = ''.join(random.choice('0123456789ABCDEF') for i in range(6))
    name = f'pbt-{random_id}'
    create(
        path=path, name=name, from_image=from_image, privileged=privileged,
        push_overlay=push_overlay, ignore_existing_containers=True
    )
    try:
        run(path_or_name=name)
    finally:
        stop(path_or_name=name)
        run_podman('rm', name, quiet=True)


def stop(path_or_name):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    if container_data['State']['Running']:
        run_podman('stop', container_name, quiet=True)
    stop_ssh_agent(container_name)


def ssh_add(path_or_name, args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    if not container_data['State']['Running']:
        status("Container is not running, so neither is its ssh-agent.")
    else:
        subprocess.run(['ssh-add', *args], env={**os.environ, "SSH_AUTH_SOCK": ssh_agent_socket(container_name)})


def name(path_or_name):
    containers_by_path, containers_by_name = get_containers()
    print(find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name))


def ls():
    run_podman('ps', '--all', '--size', '--filter', 'label=probox.project_path', '--format', 'table {{.ID}} {{.Size}} {{.Status}} {{.Names}} {{.Mounts}}')


def get_overlay_files():
    if 'home_overlay' not in config:
        return []
    home_overlay = Path(config['home_overlay'])
    return [file.relative_to(home_overlay) for file in home_overlay.rglob('*') if file.is_file()]


def push_overlay_to_container(name, files=None):
    # TODO: container needs to be running ... also it's three commands per file???
    user = getpass.getuser()
    group = grp.getgrgid(os.getgid()).gr_name

    for relfile in files or get_overlay_files():
        container_file = Path.home() / relfile
        subprocess.run(["podman", "exec", "--user", user, name, "mkdir", "-p", str(container_file.parent)], check=True)
        subprocess.run(["podman", "cp", Path(config['home_overlay']) / relfile, f"{name}:{container_file}"], check=True)
        subprocess.run(["podman", "exec", name, "chown", f"{user}:{group}", str(container_file)], check=True)


def pull_overlay_from_container(name, files=None):
    for relfile in files or get_overlay_files():
        host_file = Path(config['home_overlay']) / relfile
        host_file.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["podman", "cp", f"{name}:{Path.home() / relfile}", str(host_file)], check=True)


def overlay(path_or_name, operation, files=[]):
    if 'home_overlay' not in config:
        status(f"No 'home_overlay' set in config, can't do anything")
        sys.exit(1)

    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name)
    files = [Path(f) for f in files]
    if operation == 'push':
        push_overlay_to_container(container_name, files)
    else:
        pull_overlay_from_container(container_name, files)


ports_code = '''
import json, psutil
print(json.dumps([
    {"ip": p.laddr.ip, "port": p.laddr.port, "type": p.type, "cmd": (psutil.Process(p.pid).cmdline() if p.pid is not None else None)}
    for p in psutil.net_connections() if p.status == 'LISTEN'
]))
'''


detect_services = {
    ('/usr/lib/code-server/lib/node', '/usr/lib/code-server/out/node/entry'): ('code-server', 'http'),
}


def ports():
    # Podman doesn't track the auto-forwarded ports pasta handles, so we look for them ourselves
    running_containers = capture_podman('ps', '--filter', 'label=probox.project_path')
    for c in running_containers:
        name = c['Names'][0]
        # TODO: understand why we need --privileged exactly? Sometimes the PID is None (even though running equivalent code
        # in a shell *does* give the PID)
        open_ports = capture_podman('exec', '--privileged', name, 'python3', '-c', ports_code, format_json=False)
        print(f"- {c['Names'][0]}")
        for p in open_ports:
            service = detect_services.get(tuple(p['cmd']))
            if service is not None:
                name, proto = service
            else:
                name = Path(p['cmd'][0]).name
                proto = 'http' if p['type'] == socket.SOCK_STREAM else ''  # not all TCP is HTTP, but most?
            print(f"   - {proto}://127.0.0.1:{p['port']}/  ({name})")


def main():
    global config

    parser = argparse.ArgumentParser(prog="probox", description="Manage containers for your development projects (with podman).")

    subparsers = parser.add_subparsers()

    create_parser = subparsers.add_parser('create', help="Create a new container (box) for your project")
    create_parser.add_argument('path', nargs='?', default=None, help="Path to attach to container (default = working dir)")
    create_parser.add_argument('--name', help="Set the name for the container")
    create_parser.add_argument('--from', help="Container image to base this one upon")
    create_parser.add_argument('--no-overlay', action="store_true", help="Disable initial push of overlay files")
    create_parser.add_argument('--privileged', action="store_true", help="Make container privileged (not secure, but makes nested podman possible)")
    create_parser.set_defaults(func=lambda args: create(
        path=args.path, name=args.name, from_image=getattr(args, 'from'), privileged=args.privileged, push_overlay=not args.no_overlay
    ))

    run_parser = subparsers.add_parser('run', help="Run an existing container (start and exec)")
    run_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    run_parser.add_argument('cmd', nargs=argparse.REMAINDER, help="Command to run")
    run_parser.set_defaults(func=lambda args: run(
        path_or_name=args.path_or_name, cmd=args.cmd
    ))

    temp_parser = subparsers.add_parser('temp', help="Create and run a temporary container")
    temp_parser.add_argument('path', nargs='?', default=None, help="Path to attach to container (default = working dir)")
    temp_parser.add_argument('--from', help="Container image to base this one upon")
    temp_parser.add_argument('--no-overlay', action="store_true", help="Disable initial push of overlay files")
    temp_parser.add_argument('--privileged', action="store_true", help="Make container privileged (way less secure, but makes nested podman possible)")
    temp_parser.set_defaults(func=lambda args: temp(
        path=args.path, from_image=getattr(args, 'from'), privileged=args.privileged, push_overlay=not args.no_overlay
    ))

    stop_parser = subparsers.add_parser('stop', help="Stop a container")
    stop_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    stop_parser.set_defaults(func=lambda args: stop(path_or_name=args.path_or_name))

    ssh_add_parser = subparsers.add_parser('ssh-add', help="Add key to ssh-agent for project (tip: use -c to confirm usage in host)")
    ssh_add_parser.add_argument('path_or_name', help="Path or name of container")
    ssh_add_parser.add_argument('args', nargs=argparse.REMAINDER, help="Arguments passed to ssh-add")
    ssh_add_parser.set_defaults(func=lambda args: ssh_add(path_or_name=args.path_or_name, args=args.args))

    name_parser = subparsers.add_parser('name', help="Get name of container attached to directory")
    name_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    name_parser.set_defaults(func=lambda args: name(path_or_name=args.path_or_name))

    ls_parser = subparsers.add_parser('ls', help="List all probox containers")
    ls_parser.set_defaults(func=lambda args: ls())

    overlay_parser = subparsers.add_parser('overlay', help="Manage overlay files (useful for configs/dotfiles/...)")
    overlay_parser.add_argument("operation", choices=["push", "pull"])
    overlay_parser.add_argument('path_or_name', nargs='?', help="Path or name of container (default = working dir)")
    overlay_parser.add_argument('file', nargs='*', help="File to push or pull")
    overlay_parser.set_defaults(func=lambda args: overlay(
        path_or_name=args.path_or_name, operation=args.operation, files=args.file
    ))

    ports_parser = subparsers.add_parser('ports', help="List all exposed ports")
    ports_parser.set_defaults(func=lambda args: ports())

    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        return
    else:
        config_base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / 'probox'
        config_base.mkdir(exist_ok=True, parents=True)

        config_file = config_base / 'probox.toml'

        if not config_file.exists():
            with open(config_file, 'w') as f:
                f.write(default_config_file)
            status(f"Written default config to {config_file}")

        with config_file.open("rb") as f:
            config = tomllib.load(f)
    args.func(args)


if __name__ == "__main__":
    main()
