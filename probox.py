#!/usr/bin/env python3

import argparse, sys, os, signal, json, subprocess, tempfile, socket
from pathlib import Path
from dataclasses import dataclass, asdict

CONFIG_PATH = Path('/var/Bestanden/Configs/home')
START = '\033[1;33m>>>'
END = '\033[0m\n'
GENERIC_NAMES = {'src', 'source', 'project', 'dir', 'folder', 'git', 'repo', 'repository', 'code'}


# TODO: handle errors automatically:
#   Failed to create control group inotify object: Too many open files
#   Failed to allocate manager object: Too many open files
# Solution -> `sudo sysctl fs.inotify.max_user_instances=8192`


def capture_podman(*args, format_json=True):
    res = subprocess.run(['podman', *args, *(['--format', 'json'] if format_json else [])], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def status(*text):
    print(START, *text, end=END, file=sys.stderr)


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
    return f'/run/user/1000/probox-{name}-ssh-agent.socket'


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


def create(args):
    containers_by_path, containers_by_name = get_containers()
    proj_path = Path(os.getcwd() if args.path is None else args.path).absolute()
    if proj_path in containers_by_path:
        status("Path already registered!", containers_by_path[proj_path]['Names'][0])
        sys.exit(1)

    name = args.name or suggest_name(proj_path, containers_by_name.keys())
    if name is None:
        status("Couldn't determine a name from the path. Specify a name with --name.")
        sys.exit(1)
    if '.' in name or '/' in name:
        status("Name can't contain . or /")
        sys.exit(1)

    basic_create_options = ['--name', name, '--hostname', name, '--tz=local']

    from_image = getattr(args, 'from') or 'arch-with-code-server'
    image_data = capture_podman('image', 'inspect', from_image)[0]
    post_create_cmd = image_data["Config"]["Labels"].get("probox.post_create")

    if post_create_cmd:
        run_podman('create', *basic_create_options, from_image, quiet=True)
        try:
            run_podman('start', name, quiet=True)
            run_podman('exec', '-it', name, post_create_cmd, check=True)
            # Why all this work? See https://github.com/containers/podman/issues/18309
            res = run_podman('commit', name, '--pause=true', capture_output=True)
            # Next command will recreate it
            from_image = res.stdout.strip()

            with tempfile.TemporaryDirectory() as tempdir:
                extra_options_filename = Path(tempdir) / 'extra_podman_options.json'
                cp_res = run_podman('cp', f'{name}:/extra_podman_options.json', str(extra_options_filename), check=False, quiet=True)
                if cp_res.returncode == 0:
                    with open(extra_options_filename) as f:
                        extra_podman_options = json.load(f)
                else:
                    extra_podman_options = []
        finally:
            run_podman('stop', name, quiet=True)
            run_podman('rm', name, quiet=True)

    else:
        extra_podman_options = []

    # TODO get rid of --security-opt label=disable ???
    # how does toolbx do it? the example given in docs specifically mentions "mounting entire home directory"
    # https://docs.podman.io/en/latest/markdown/podman-run.1.html#volume-v-source-volume-host-dir-container-dir-options

    # TODO look at https://github.com/containers/podman/discussions/13728#discussioncomment-2900471
    # In particular, this comment says something like using --userns=auto "with a huge /etc/subuid range"
    # Already did the subuid thing via
    #   sudo usermod --add-subuids 1000000-990000000 --add-subgids 1000000-990000000 evert
    # But then mapping the volume is impossible (I'd use `:idmap=uids=1000-1000-1;gids=1000-1000-1`), see https://github.com/containers/crun/issues/1632

    start_ssh_agent(name)

    run_podman(
        'create', *basic_create_options, '--label', f'probox.project_path={proj_path}',
        '--userns=keep-id', '--security-opt', 'label=disable',
        '--pids-limit=-1',
        *(['--privileged'] if args.privileged else []),
        '--volume', f'{proj_path}:{proj_path}',
        '--volume', f'{ssh_agent_socket(name)}:/home/evert/ssh-agent.socket',
        # pasta: auto forward ports from container to host, but not other way around
        # WARNING: binding on 0.0.0.0 in a container will ALSO expose it on 0.0.0.0 on the host!
        # I use a firewall to fix this, so I can also temporarily allow it (e.g. to allow my phone on WiFi to run a webapp)
        '--network=pasta:-t,auto,-u,auto,-T,none,-U,none',
        *extra_podman_options,
        from_image
    )

    if not args.noconfig:
        run_podman('start', name, quiet=True)
        push_configs_to_container(name)


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


def run(args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    project_path = Path(container_data['Config']['Labels']['probox.project_path'])

    if not container_data['State']['Running']:
        start_ssh_agent(container_name)
        run_podman('start', container_name, quiet=True)

    cwd = Path(os.getcwd()).absolute()
    if project_path == cwd or project_path in cwd.parents:
        workdir = cwd
    else:
        workdir = Path('/home/evert')

    if args.cmd:
        cmd = args.cmd
    else:
        cmd = ['/bin/fish', '-l']  # -l for login shell

    env = {
        'SSH_AUTH_SOCK': '/home/evert/ssh-agent.socket',
        # Assume linger in systemd
        'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus',
        'XDG_RUNTIME_DIR': '/run/user/1000',
        'PWD': workdir,
    }

    with tempfile.NamedTemporaryFile(mode='w+') as f:
        for k, v in env.items():
            f.write(f"{k}={v}\n")
        f.flush()
        run_podman('exec', '-it', '--user', 'evert', '--workdir', str(workdir), '--env-file', f.name, container_name, *cmd, check=False)


def stop(args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    if container_data['State']['Running']:
        run_podman('stop', container_name, quiet=True)
    stop_ssh_agent(container_name)


def ssh_add(args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    if not container_data['State']['Running']:
        status("Container is not running, so neither is its ssh-agent.")
    else:
        subprocess.run(['ssh-add', *args.args], env={**os.environ, "SSH_AUTH_SOCK": ssh_agent_socket(container_name)})


def name(args):
    containers_by_path, containers_by_name = get_containers()
    print(find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name))


def ls(args):
    run_podman('ps', '--all', '--size', '--filter', 'label=probox.project_path', '--format', 'table {{.ID}} {{.Size}} {{.Status}} {{.Names}} {{.Mounts}}')


def get_config_files():
    return [file.relative_to(CONFIG_PATH) for file in CONFIG_PATH.rglob('*') if file.is_file()]


def push_configs_to_container(name, files=None):
    # TODO: container needs to be running ... also it's three commands per file???
    for relfile in files or get_config_files():
        container_file = Path('/home/evert') / relfile
        subprocess.run(["podman", "exec", "--user", "evert", name, "mkdir", "-p", str(container_file.parent)], check=True)
        subprocess.run(["podman", "cp", CONFIG_PATH / relfile, f"{name}:{container_file}"], check=True)
        subprocess.run(["podman", "exec", name, "chown", "evert:evert", str(container_file)], check=True)


def pull_configs_from_container(name, files=None):
    for relfile in files or get_config_files():
        host_file = CONFIG_PATH / relfile
        host_file.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["podman", "cp", f"{name}:{Path('/home/evert') / relfile}", str(host_file)], check=True)


def config(args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name)
    files = [Path(f) for f in args.file]
    if args.operation == 'push':
        push_configs_to_container(container_name, files)
    else:
        pull_configs_from_container(container_name, files)


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


def ports(args):
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
    parser = argparse.ArgumentParser(prog="probox", description="Manage containers for your development projects (with podman).")

    subparsers = parser.add_subparsers()

    create_parser = subparsers.add_parser('create', help="Create a new container (box) for your project")
    create_parser.add_argument('path', nargs='?', default=None, help="Path to attach to container (default = working dir)")
    create_parser.add_argument('--name', help="Set the name for the container")
    create_parser.add_argument('--from', help="Container image to base this one upon")
    create_parser.add_argument('--noconfig', action="store_true", help="Disable initial config push")
    create_parser.add_argument('--privileged', action="store_true", help="Make container privileged (way less secure, but makes nested podman possible)")
    create_parser.set_defaults(func=create)

    run_parser = subparsers.add_parser('run', help="Run an existing container (start and exec)")
    run_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    run_parser.add_argument('cmd', nargs=argparse.REMAINDER, help="Command to run")
    run_parser.set_defaults(func=run)

    stop_parser = subparsers.add_parser('stop', help="Stop a container")
    stop_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    stop_parser.set_defaults(func=stop)

    ssh_add_parser = subparsers.add_parser('ssh-add', help="Add key to ssh-agent for project (tip: use -c to confirm usage in host)")
    ssh_add_parser.add_argument('path_or_name', help="Path or name of container")
    ssh_add_parser.add_argument('args', nargs=argparse.REMAINDER, help="Arguments passed to ssh-add")
    ssh_add_parser.set_defaults(func=ssh_add)

    name_parser = subparsers.add_parser('name', help="Get name of container attached to directory")
    name_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    name_parser.set_defaults(func=name)

    ls_parser = subparsers.add_parser('ls', help="List all probox containers")
    ls_parser.set_defaults(func=ls)

    config_parser = subparsers.add_parser('config', help="Manage configuration files")
    config_parser.add_argument("operation", choices=["push", "pull"])
    config_parser.add_argument('path_or_name', nargs='?', help="Path or name of container (default = working dir)")
    config_parser.add_argument('file', nargs='*', help="File to push or pull")
    config_parser.set_defaults(func=config)

    ports_parser = subparsers.add_parser('ports', help="List all exposed ports")
    ports_parser.set_defaults(func=ports)

    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
