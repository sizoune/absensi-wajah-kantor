#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$script_dir/Absensi" install
printf '%s\n' 'Buka Kioska Absensi Kantor dari menu aplikasi untuk memulai wizard.'
