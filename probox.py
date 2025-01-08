#!/usr/bin/env python3

from pathlib import Path
import argparse
import sys
import os
import json
from dataclasses import dataclass, asdict
import subprocess
import random

boxes_dir = Path("/var/Bestanden/Boxes")


def capture_podman(*args):
    res = subprocess.run(['podman', *args, '--format', 'json'], capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def run_podman(*args, check=True, **kwargs):
    command = ['podman', *args]
    print(">>> ", ' '.join(command))
    return subprocess.run(command, check=check, **kwargs)


# Only the ones with a name (TODO versioned local images?)
def reset():
    global probox_images, probox_images_by_path, probox_images_by_boxname, box_names
    probox_images = [im for im in capture_podman('images', '--filter', 'label=probox.project_path') if im.get('Names')]

    probox_images_by_path = {
        Path(im['Labels']['probox.project_path']).absolute(): im
        for im in probox_images
    }
    probox_images_by_boxname = {
        im['Labels']['probox.name']: im
        for im in probox_images
    }

    box_names = [box_dir.name for box_dir in boxes_dir.iterdir()]


reset()


def suggest_name(path):
    abspath = path.absolute()
    if abspath.name not in box_names:
        return path.name

    # boxes_at_higher_dir = set(abspath.parents) & probox_images_by_path.keys()

    # if len(boxes_at_higher_dir) == 0:
    # combo = abspath.parent.name + '-' + abspath.name
    # if combo not in box_names:
    #     return combo

    i = 2
    while True:
        name_and_number = abspath.name + f"-{i:>03}"
        if name_and_number not in box_names:
            return name_and_number
        i += 1


def create(args):
    path = Path(os.getcwd() if args.path is None else args.path).absolute()
    if path in probox_images_by_path:
        print("Path already registered!", probox_images_by_path[path]['Names'][0])
        sys.exit(1)

    name = args.name or suggest_name(path)
    if name is None:
        print("Couldn't determine a name from the path. Specify a name with --name.")
        sys.exit(1)
    if name in box_names:
        print(f"Name '{name}' is already taken.")
        sys.exit(1)

    from_name_or_path = getattr(args, 'from') or 'base'

    if '/' in from_name_or_path:
        from_image_path = Path(from_name_or_path).absolute()
        # No parent dir logic here
        image = probox_images_by_path.get(str(from_image_path))
        if image is not None:
            from_image_name = image["Names"][0]
    elif from_name_or_path in box_names:
        from_image_name = f"localhost/{from_name_or_path}:latest"

    box_dir = boxes_dir / name
    box_dir.mkdir()

    labels = {
        'project_path': path,
        'name': name,
    }

    # write Containerfile
    containerfile_path = box_dir / 'Containerfile'
    with open(containerfile_path, 'w') as f:
        f.write(f"FROM {from_image_name}\n")

        for label, value in labels.items():
            f.write(f"LABEL probox.{label} {str(value)}\n")

    print(f">>> Wrote file://{containerfile_path}")

    run_podman('build', str(boxes_dir / name), '-t', name.lower())


# build
# podman build . -t arch-base

# run with port mappings
# podman run -p 8000-9000:8000-9000 -i -t arch-base

# volume?
# podman volume create --driver local --opt type=none --opt device=/path/to/host-dir --opt o=bind FOO

# run shell in running container
# podman exec -i -t --user evert --env XDG_RUNTIME_DIR=/run/user/1000 --env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus --workdir /home/evert dreamy_knuth


def find_box_name_by_path_or_name(path_or_name):
    if path_or_name is None or '/' in path_or_name:
        deep_path = Path(path_or_name or os.getcwd()).absolute()
        print("Searching", deep_path, 'in', probox_images_by_path.keys())
        for path in [deep_path] + list(deep_path.parents):
            image = probox_images_by_path.get(path
            if image is not None:
                return image["Labels"]['probox.name']

    if path_or_name not in box_names:
        print(f"Couldn't find name '{path_or_name}'")
        sys.exit(1)


def get_ports(image):
    return {
        label[len('probox.ports.'):]: int(val)
        for label, val in image['Labels'].items() if label.startswith('probox.ports.')
    }


def run(args):
    box_name = find_box_name_by_path_or_name(args.path_or_name)
    print(f">>> Found box '{box_name}'")
    image = probox_images_by_boxname[box_name]
    box_path = boxes_dir / box_name
    project_path = Path(image['Labels']['probox.project_path'])

    # check if exists
    containers = capture_podman('ps', '--filter', f'label=probox.name={box_name}')

    if len(running) == 0:
        # build image
        run_podman('build', str(boxes_dir / box_name), '-t', box_name.lower())
        reset()

        # # check if a volume exists
        # existing_volumes = capture_podman('volume', 'ls', '--filter', f'label=probox.name={box_name}')
        #
        # if len(existing_volumes) == 0:
        #     volume_name = f'{box_name}-{random.randint(0, 0xFFFF):04x}'
        #     # TODO put volume in Boxes??
        #     # ... '--driver', 'local', '--opt', f'device={str(box_path / '_root')}',
        #     run_podman('volume', 'create', volume_name, '--label', f"probox.name={box_name}")
        # elif len(existing_volumes) == 1:
        #     volume_name = existing_volumes[0]["Name"]
        # else:
        #     print("Found multiple volumes for this box")
        #     sys.exit(1)
        # '--volume', f'{volume_name}:/home', ???

        # run in background
        ports = []
        for p in get_ports(image).values():
            print("PORT", p)
            ports.extend(['--publish', f'{p}:{p}'])

        running_name = f"{box_name}-{random.randint(0, 0xFFFF):04x}"

        # TODO get rid of --security-opt label=disable ???
        # how does toolbx do it? the example given in docs specifically mentions "mounting entire home directory"
        # https://docs.podman.io/en/latest/markdown/podman-run.1.html#volume-v-source-volume-host-dir-container-dir-options
        run_podman('run', '--userns=keep-id', *ports, '--security-opt', 'label=disable', '--volume', f'{project_path}:{project_path}', '--name', running_name, '--hostname', running_name, '--detach', image['Names'][0])
    elif len(running) > 1:
        print('Weird: more than one container running: ', ', '.join(r['Names'][0] for r in running))
        sys.exit(1)
    else:
        running_name = running[0]['Names'][0]

    cwd = Path(os.getcwd()).absolute()
    if project_path == cwd or project_path in cwd.parents:
        workdir = cwd
    else:
        workdir = Path('/home/evert')

    run_podman('exec', '-it', '--user', 'evert', '--workdir', str(workdir), '--env', 'XDG_RUNTIME_DIR=/run/user/1000', '--env', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', running_name, '/bin/zsh', check=False)


def ps(args):
    run_podman('ps', '--filter', 'label=probox.name')


def stop(args):
    box_name = find_box_name_by_path_or_name(args.path_or_name)
    running = capture_podman('ps', '--filter', f'label=probox.name={box_name}')
    for r in running:
        run_podman('stop', r['Names'][0])


def main():
    parser = argparse.ArgumentParser(prog="probox", description="Manage containers for your development projects (with podman).")

    subparsers = parser.add_subparsers()

    # 'create' command
    create_parser = subparsers.add_parser('create', help="Create a new container (box) for your project")
    create_parser.add_argument('path', nargs='?', default=None, help="Path to attach to container (default = working dir)")
    create_parser.add_argument('--name', help="Set the name for the container")
    create_parser.add_argument('--from', help="Container image to base this one upon (path or name)")
    create_parser.set_defaults(func=create)

    # 'run' command
    run_parser = subparsers.add_parser('run', help="Run an existing container")
    run_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container to run (default = working dir)")
    run_parser.set_defaults(func=run)

    # 'ps' command
    ps_parser = subparsers.add_parser('ps', help="List all containers")
    ps_parser.set_defaults(func=ps)

    # 'stop' command
    stop_parser = subparsers.add_parser('stop', help="Stop a container")
    stop_parser.add_argument('path_or_name', nargs='?', default=None, help="Path or name of container to stop")
    stop_parser.set_defaults(func=stop)

    # Parse and run the appropriate function, or show help
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        return
    args.func(args)



if __name__ == "__main__":
    main()
