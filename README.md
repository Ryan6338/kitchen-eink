# Kitchen display configuration

This repository keeps the kitchen display's ESPHome and Home Assistant sources
separate and deploys them over SSH with `just`.

## Layout

- `esphome/` contains only the two real device entry points at its root, so the
  ESPHome dashboard does not show supporting files as devices.
- `esphome/includes/` contains shared LVGL, refresh, and rendering YAML.
- `esphome/assets/` contains fonts and images.
- `home-assistant/packages/` contains Home Assistant packages.
- `home-assistant/scripts/` contains one source file per managed script. During
  deployment these are assembled into `/config/packages/kitchen_scripts.yaml`.
- `home-assistant/custom_components/`, `custom_sentences/`, and `dashboards/`
  mirror their remote `/config` destinations.

## Deploy

The dedicated key defaults to
`~/.ssh/home_assistant_deploy_ed25519`, and the host defaults to
`root@192.168.68.200`.

```sh
just check
just deploy
```

`just deploy` creates a timestamped backup under
`/config/backups/kitchen-eink/`, uploads both configuration groups, runs
`ha core check`, and restarts Home Assistant Core only when the check passes.
It preserves unrelated entries in `/config/scripts.yaml`; the eight scripts in
`home-assistant/scripts/` are migrated into the generated package.

More focused commands are available:

```sh
just deploy-ha
just deploy-esphome
just check-remote
just ssh
```

Override the connection without editing the repository:

```sh
HA_HOST=root@homeassistant.local \
HA_SSH_KEY="$HOME/.ssh/home_assistant_deploy_ed25519" \
just deploy
```

Set `HA_RESTART=0` to upload and validate Home Assistant files without applying
them immediately.

`just check` performs fast source validation. ESPHome's full device validation
should use the Device Builder version installed on Home Assistant; an older
local ESPHome may reject newer LVGL options such as `bottom_layer`.
