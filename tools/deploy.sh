#!/usr/bin/env bash
set -euo pipefail

mode="${1:-all}"
case "$mode" in
  all|home-assistant|esphome) ;;
  *) echo "usage: $0 [all|home-assistant|esphome]" >&2; exit 2 ;;
esac

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ha_host="${HA_HOST:-root@192.168.68.200}"
ha_key="${HA_SSH_KEY:-${HOME}/.ssh/home_assistant_deploy_ed25519}"

if [[ ! -r "$ha_key" ]]; then
  echo "deployment key not found: $ha_key" >&2
  exit 1
fi

ssh_options=(
  -i "$ha_key"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
)
scp_options=(
  -i "$ha_key"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
)

run_remote() {
  ssh "${ssh_options[@]}" "$ha_host" "$@"
}

temp_dir="$(mktemp -d -t kitchen-eink-deploy.XXXXXX)"
trap 'rm -rf "$temp_dir"' EXIT

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="/config/backups/kitchen-eink/$timestamp"

echo "Checking SSH access to $ha_host..."
run_remote 'test -d /config'

echo "Creating remote backup at $backup_dir..."
run_remote "backup_dir='$backup_dir'; mkdir -p \"\$backup_dir\"; cd /config; for path in packages/kitchen_recipes.yaml packages/meal_planner.yaml packages/weather.yaml packages/kitchen_scripts.yaml scripts.yaml custom_components/kitchen_meal_planner custom_components/woolworths_specials custom_sentences/en/meal_planner.yaml dashboards/meal-planner.yaml esphome/evie-office-display.yaml esphome/evie-office-display-simulator.yaml esphome/kitchen-display.yaml esphome/kitchen-display-simulator.yaml esphome/kitchen-simulator.yaml esphome/kitchen-common.yaml esphome/kitchen-lvgl.yaml esphome/kitchen-lvgl-refresh.yaml esphome/kitchen-render.yaml esphome/includes esphome/assets; do if [ -e \"\$path\" ]; then mkdir -p \"\$backup_dir/\$(dirname \"\$path\")\"; cp -a \"\$path\" \"\$backup_dir/\$path\"; else printf '%s\\n' \"\$path\" >> \"\$backup_dir/absent\"; fi; done"

if [[ "$mode" == "all" || "$mode" == "esphome" ]]; then
  echo "Deploying ESPHome device configs, includes, and assets..."
  tar -C "$repo_dir/esphome" -cf - . | run_remote 'mkdir -p /config/esphome; tar -C /config/esphome -xf -; rm -f /config/esphome/kitchen-simulator.yaml /config/esphome/kitchen-common.yaml /config/esphome/kitchen-lvgl.yaml /config/esphome/kitchen-lvgl-refresh.yaml /config/esphome/kitchen-render.yaml'
fi

if [[ "$mode" == "all" || "$mode" == "home-assistant" ]]; then
  echo "Building the Home Assistant script package..."
  python3 "$repo_dir/tools/build_script_package.py" \
    "$repo_dir/home-assistant/scripts" \
    "$temp_dir/kitchen_scripts.yaml"

  echo "Migrating managed scripts out of scripts.yaml..."
  scp "${scp_options[@]}" "$ha_host:/config/scripts.yaml" "$temp_dir/scripts.yaml"
  python3 "$repo_dir/tools/remove_managed_scripts.py" \
    "$temp_dir/scripts.yaml" \
    "$repo_dir/home-assistant/scripts" \
    "$temp_dir/scripts-clean.yaml"
  scp "${scp_options[@]}" "$temp_dir/scripts-clean.yaml" "$ha_host:/config/scripts.yaml"

  echo "Deploying Home Assistant packages, integration, sentences, and dashboard source..."
  tar -C "$repo_dir/home-assistant" -cf - packages custom_components custom_sentences dashboards \
    | run_remote 'mkdir -p /config/packages /config/custom_components /config/custom_sentences /config/dashboards; tar -C /config -xf -'
  scp "${scp_options[@]}" "$temp_dir/kitchen_scripts.yaml" "$ha_host:/config/packages/kitchen_scripts.yaml"

  echo "Running Home Assistant configuration check..."
  if ! run_remote 'ha core check'; then
    echo "Configuration check failed; restoring Home Assistant files from $backup_dir..." >&2
    run_remote "backup_dir='$backup_dir'; cd /config; for path in packages/kitchen_recipes.yaml packages/meal_planner.yaml packages/weather.yaml packages/kitchen_scripts.yaml scripts.yaml custom_components/kitchen_meal_planner custom_components/woolworths_specials custom_sentences/en/meal_planner.yaml dashboards/meal-planner.yaml; do rm -rf \"/config/\$path\"; if [ -e \"\$backup_dir/\$path\" ]; then mkdir -p \"/config/\$(dirname \"\$path\")\"; cp -a \"\$backup_dir/\$path\" \"/config/\$path\"; fi; done"
    exit 1
  fi

  if [[ "${HA_RESTART:-1}" == "1" ]]; then
    echo "Configuration is valid; restarting Home Assistant Core..."
    run_remote 'ha core restart'
  else
    echo "Configuration is valid; HA_RESTART=0 left Home Assistant Core running."
  fi
fi

echo "Deployment complete. Backup: $backup_dir"
