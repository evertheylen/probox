#!/usr/bin/env python3

from pathlib import Path
import argparse
import sys
import os
import json
from dataclasses import dataclass, asdict
import subprocess
import random
import tempfile

YELLOW = '\033[1;33m'
END = '\033[0m'
GENERIC_NAMES = {'src', 'source', 'project', 'dir', 'folder', 'git', 'repo', 'repository'}


def capture_podman(*args):
    res = subprocess.run(['podman', *args, '--format', 'json'], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def run_podman(*args, check=True, quiet=False, **kwargs):
    command = ['podman', *args]
    print(f"{YELLOW}>>> {' '.join(command)}{END}")
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


def create(args):
    containers_by_path, containers_by_name = get_containers()
    path = Path(os.getcwd() if args.path is None else args.path).absolute()
    if path in containers_by_path:
        print("Path already registered!", containers_by_path[path]['Names'][0])
        sys.exit(1)

    name = args.name or suggest_name(path, containers_by_name.keys())
    if name is None:
        print("Couldn't determine a name from the path. Specify a name with --name.")
        sys.exit(1)

    basic_create_options = ['--name', name, '--hostname', name]

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
                cp_res = run_podman('cp', f'{name}:/extra_podman_options.json', str(extra_options_filename), check=False)
                if cp_res.returncode == 0:
                    with open(extra_options_filename) as f:
                        extra_podman_options = json.load(f)
        finally:
            run_podman('stop', name, quiet=True)
            run_podman('rm', name, quiet=True)

    else:
        extra_podman_options = []

    # TODO get rid of --security-opt label=disable ???
    # how does toolbx do it? the example given in docs specifically mentions "mounting entire home directory"
    # https://docs.podman.io/en/latest/markdown/podman-run.1.html#volume-v-source-volume-host-dir-container-dir-options

    run_podman(
        'create', *basic_create_options, '--label', f'probox.project_path={path}',
        '--userns=keep-id', '--security-opt', 'label=disable',
        '--volume', f'{path}:{path}',
        *extra_podman_options,
        from_image
    )


def find_container_name_by_path_or_name(containers_by_path, containers_by_name, path_or_name):
    if path_or_name is None or '/' in path_or_name:
        deep_path = Path(path_or_name or os.getcwd()).absolute()
        for path in [deep_path] + list(deep_path.parents):
            con = containers_by_path.get(path)
            if con is not None:
                return con["Names"][0]
        print("No container found for directory", deep_path, 'in', containers_by_path.keys())
        sys.exit(1)

    if path_or_name not in containers_by_name:
        print(f"Couldn't find name '{path_or_name}'")
        sys.exit(1)

    return path_or_name


def run(args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    project_path = Path(container_data['Config']['Labels']['probox.project_path'])

    if not container_data['State']['Running']:
        run_podman('start', container_name, quiet=True)

    cwd = Path(os.getcwd()).absolute()
    if project_path == cwd or project_path in cwd.parents:
        workdir = cwd
    else:
        workdir = Path('/home/evert')

    run_podman('exec', '-it', '--user', 'evert', '--workdir', str(workdir), '--env', 'XDG_RUNTIME_DIR=/run/user/1000', '--env', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', container_name, '/bin/zsh', check=False)


def stop(args):
    containers_by_path, containers_by_name = get_containers()
    container_name = find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name)

    container_data = capture_podman('container', 'inspect', container_name)[0]
    if container_data['State']['Running']:
        run_podman('stop', container_name)


def name(args):
    containers_by_path, containers_by_name = get_containers()
    print(find_container_name_by_path_or_name(containers_by_path, containers_by_name, args.path_or_name))


def ls(args):
    run_podman('ps', '--all', '--size', '--filter', 'label=probox.project_path', '--format', 'table {{.ID}} {{.Size}} {{.Status}} {{.Ports}} {{.Names}} {{.Mounts}}')


def main():
    parser = argparse.ArgumentParser(prog="probox", description="Manage containers for your development projects (with podman).")

    subparsers = parser.add_subparsers()

    # 'create' command
    create_parser = subparsers.add_parser('create', help="Create a new container (box) for your project")
    create_parser.add_argument('path', nargs='?', default=None, help="Path to attach to container (default = working dir)")
    create_parser.add_argument('--name', help="Set the name for the container")
    create_parser.add_argument('--from', help="Container image to base this one upon")
    create_parser.set_defaults(func=create)

    # 'run' command
    run_parser = subparsers.add_parser('run', help="Run an existing container (start and exec)")
    run_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    run_parser.set_defaults(func=run)

    # 'stop' command
    stop_parser = subparsers.add_parser('stop', help="Stop a container")
    stop_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    stop_parser.set_defaults(func=stop)

    # 'name' command
    name_parser = subparsers.add_parser('name', help="Get name of container attached to directory")
    name_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container (default = working dir)")
    name_parser.set_defaults(func=name)

    # 'ls' command
    ls_parser = subparsers.add_parser('ls', help="List all probox containers")
    ls_parser.set_defaults(func=ls)

    # Parse and run the appropriate function, or show help
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
