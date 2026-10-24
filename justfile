set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

ha_host := env_var_or_default("HA_HOST", "root@192.168.68.200")
ha_key := env_var_or_default("HA_SSH_KEY", env_var("HOME") + "/.ssh/home_assistant_deploy_ed25519")
simulator_esphome := ".venv/bin/esphome"

# Show the available project commands.
default:
    @just --list

# Validate all local YAML, JSON, and Python files.
check:
    @python3 tools/validate.py

# Install the simulator's native and Python dependencies.
setup-simulator:
    @tools/setup-simulator.sh

# Build and run the kitchen display simulator (run setup-simulator first).
simulator *args:
    @test -x '{{simulator_esphome}}' || { echo 'Simulator dependencies are missing; run `just setup-simulator` first.' >&2; exit 1; }
    @'{{simulator_esphome}}' run esphome/kitchen-display-simulator.yaml {{args}}

# Build the Evie office display desktop simulator without launching it.
evie-simulator-build:
    @test -x '{{simulator_esphome}}' || { echo 'Simulator dependencies are missing; run `just setup-simulator` first.' >&2; exit 1; }
    @'{{simulator_esphome}}' compile esphome/evie-office-display-simulator.yaml

# Build and run the Evie office display desktop simulator.
evie-simulator *args:
    @test -x '{{simulator_esphome}}' || { echo 'Simulator dependencies are missing; run `just setup-simulator` first.' >&2; exit 1; }
    @'{{simulator_esphome}}' run esphome/evie-office-display-simulator.yaml {{args}}

# Deploy everything and restart Home Assistant after a successful config check.
deploy: check
    @HA_HOST='{{ha_host}}' HA_SSH_KEY='{{ha_key}}' tools/deploy.sh all

# Deploy Home Assistant packages, scripts, and integrations, then restart Core.
deploy-ha: check
    @HA_HOST='{{ha_host}}' HA_SSH_KEY='{{ha_key}}' tools/deploy.sh home-assistant

# Copy ESPHome device configs, includes, and assets without restarting HA Core.
deploy-esphome: check
    @HA_HOST='{{ha_host}}' HA_SSH_KEY='{{ha_key}}' tools/deploy.sh esphome

# Validate the currently installed Home Assistant configuration.
check-remote:
    @ssh -i '{{ha_key}}' -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes '{{ha_host}}' 'ha core check'

# Open a key-authenticated shell on Home Assistant.
ssh:
    @ssh -i '{{ha_key}}' -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes '{{ha_host}}'
