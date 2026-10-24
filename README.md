# ESPHome display and Home Assistant configuration

This repository keeps the kitchen and office display ESPHome sources separate
from the Home Assistant configuration and deploys them over SSH with `just`.

## Layout

- `esphome/` contains only device and simulator entry points at its root, so
  the ESPHome dashboard does not show supporting files as devices.
- `esphome/evie-office-display.yaml` is the standalone 800×480 configuration
  for the Guition JC8048W550 / ESP32-8048S050C_I office display. It requires
  ESPHome 2026.5.0 or newer and includes a clock, network status, dimmable
  backlight, and on-screen touch test.
- `esphome/includes/` contains shared LVGL, refresh, and rendering YAML.
- `esphome/assets/` contains fonts and images.
- `home-assistant/packages/` contains Home Assistant packages.
- `home-assistant/scripts/` contains one source file per managed script. During
  deployment these are assembled into `/config/packages/kitchen_scripts.yaml`.
- `home-assistant/custom_components/`, `custom_sentences/`, and `dashboards/`
  mirror their remote `/config` destinations.

The balanced meal-selection engine lives in
`home-assistant/custom_components/kitchen_meal_planner/`. Home Assistant YAML
keeps only thin compatibility scripts for the dashboard and ESPHome display;
filtering, full-recipe ingredient scoring, caching, and diagnostics are handled
in testable Python. Shopping-list writes are also batched there so Mealie is
refreshed once per generated list rather than once per ingredient.

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

## Simulator

Install the simulator toolchain and its pinned ESPHome version, then run it:

```sh
just setup-simulator
just simulator
just evie-simulator-build
just evie-simulator
```

The setup command supports Debian/Ubuntu and macOS. It installs SDL2, libsodium,
the native build tools, and a project-local Python environment. macOS requires
Homebrew; if the Xcode Command Line Tools are missing, setup opens their
installer and asks you to rerun the command after installation completes.
