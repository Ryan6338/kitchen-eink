set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

ha_host := env_var_or_default("HA_HOST", "root@192.168.68.200")
ha_key := env_var_or_default("HA_SSH_KEY", env_var("HOME") + "/.ssh/home_assistant_deploy_ed25519")

# Show the available project commands.
default:
    @just --list

# Validate all local YAML, JSON, and Python files.
check:
    @python3 tools/validate.py

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
