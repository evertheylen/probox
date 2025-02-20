
# Tests

Automated tests would be nice but that would require a very complex test environment. So I'm doing this manually:

- Does code-server work?
- Does the setup-user flow run again when the base image is updated?
- Can you read and write files in the container, and are they owned by the same user as on the host?
- In the container, you have to be able to:
  - `ping 1.1.1.1`
  - `podman run --rm -it alpine`
