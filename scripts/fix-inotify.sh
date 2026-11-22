#!/usr/bin/env bash
# Raise inotify limits for a multi-node kind cluster running Prometheus + Chaos Mesh,
# and persist them across reboots. Needs sudo.
set -e
printf 'fs.inotify.max_user_watches=524288\nfs.inotify.max_user_instances=512\n' | sudo tee /etc/sysctl.d/99-kind.conf
sudo sysctl --system >/dev/null
sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances
