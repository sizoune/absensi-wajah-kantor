#!/bin/sh
set -eu
cam_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$cam_root"
exec env -u EOFFICE_BASE_URL -u EOFFICE_DEVICE_TOKEN -u EOFFICE_TEST_MODE \
    -u EOFFICE_HOST_MODEL_DIR -u EOFFICE_HOST_KEY_FILE \
    docker compose --env-file docker/compose.env \
    -f compose.yaml -f docker/compose.eoffice.yaml "$@"
