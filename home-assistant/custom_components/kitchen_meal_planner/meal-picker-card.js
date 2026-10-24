class KitchenMealPickerCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.slot || !config.config_entry_id) {
      throw new Error("Kitchen meal picker requires slot and config_entry_id");
    }
    this.config = {
      catalog_entity: "sensor.kitchen_meal_recipe_catalog",
      ...config,
    };
    this._draft = null;
    this._catalogSignature = "";
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 1;
  }

  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const slot = Number(this.config.slot);
    const listId = `kitchen-meals-${slot}`;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 14px 16px; }
        .title { color: var(--primary-text-color); font-weight: 600; margin-bottom: 9px; }
        .row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto; gap: 8px; align-items: center; }
        input {
          box-sizing: border-box; width: 100%; min-width: 0; height: 40px;
          border: 1px solid var(--divider-color); border-radius: 10px;
          background: var(--card-background-color); color: var(--primary-text-color);
          padding: 0 12px; font: inherit;
        }
        input:focus { border-color: var(--primary-color); outline: 2px solid color-mix(in srgb, var(--primary-color) 25%, transparent); }
        input:disabled { opacity: .7; cursor: not-allowed; }
        button {
          height: 40px; border: 0; border-radius: 10px; padding: 0 13px;
          background: var(--secondary-background-color); color: var(--primary-text-color);
          font: inherit; font-weight: 600; cursor: pointer;
        }
        button.primary { background: var(--primary-color); color: var(--text-primary-color, white); }
        button:disabled { opacity: .45; cursor: not-allowed; }
        .hint { color: var(--secondary-text-color); font-size: 12px; margin-top: 7px; }
        @media (max-width: 520px) {
          .row { grid-template-columns: minmax(0, 1fr) auto; }
        }
      </style>
      <ha-card>
        <div class="title">Meal ${slot}</div>
        <div class="row">
          <input type="search" list="${listId}" autocomplete="off"
            placeholder="Search recipes or type a custom meal" aria-label="Meal ${slot}">
          <datalist id="${listId}"></datalist>
          <button class="save primary" type="button">Save</button>
          <button class="lock" type="button">Lock in</button>
          <button class="open" type="button">Open</button>
        </div>
        <div class="hint">Choose a Mealie recipe, or type any meal name. Save before opening.</div>
      </ha-card>`;

    this._input = this.shadowRoot.querySelector("input");
    this._list = this.shadowRoot.querySelector("datalist");
    this._save = this.shadowRoot.querySelector("button.save");
    this._lock = this.shadowRoot.querySelector("button.lock");
    this._open = this.shadowRoot.querySelector("button.open");
    this._input.addEventListener("input", () => {
      this._draft = this._input.value;
      this._updateButtons();
    });
    this._input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        this._setMeal(false);
      }
    });
    this._save.addEventListener("click", () => this._setMeal(false));
    this._lock.addEventListener("click", () => this._toggleLock());
    this._open.addEventListener("click", () => this._openMeal());
    this._update();
  }

  _entities() {
    const slot = Number(this.config.slot);
    return {
      meal: `input_text.kitchen_meal_${slot}`,
      lock: `input_boolean.kitchen_meal_lock_${slot}`,
    };
  }

  _update() {
    if (!this._hass || !this._input) return;
    const entities = this._entities();
    const current = this._hass.states[entities.meal]?.state || "";
    const slug =
      this._hass.states[`input_text.kitchen_meal_${this.config.slot}_slug`]?.state || "";
    const locked = this._hass.states[entities.lock]?.state === "on";
    if (locked && this._locked === false) this._draft = null;
    const inputFocused = this.shadowRoot.activeElement === this._input;
    if (this._draft === null || !inputFocused) {
      this._input.value = this._draft ?? current;
    }
    this._input.disabled = locked;

    const recipes = this._hass.states[this.config.catalog_entity]?.attributes?.recipes || [];
    const signature = recipes.map((item) => `${item.slug}\u0000${item.name}`).join("\u0001");
    if (signature !== this._catalogSignature) {
      this._catalogSignature = signature;
      this._list.replaceChildren(
        ...recipes.map((item) => {
          const option = document.createElement("option");
          option.value = item.name;
          return option;
        })
      );
    }

    this._locked = locked;
    this._current = current;
    this._slug = slug;
    this._lock.textContent = locked ? "Unlock" : "Lock in";
    this._lock.classList.toggle("primary", !locked);
    this._updateButtons();
  }

  _updateButtons() {
    if (!this._input || !this._save) return;
    this._save.disabled = this._locked || !this._input.value.trim();
    this._lock.disabled = !this._locked && !this._input.value.trim();
    const hasUnsavedChanges = this._input.value.trim() !== this._current;
    this._open.disabled =
      hasUnsavedChanges || !this._slug || this._slug.startsWith("custom:");
  }

  async _setMeal(lock) {
    const value = this._input.value.trim();
    if (!value || this._locked) return;
    this._save.disabled = true;
    this._lock.disabled = true;
    try {
      await this._hass.callService("kitchen_meal_planner", "set_meal", {
        config_entry_id: this.config.config_entry_id,
        slot: Number(this.config.slot),
        value,
        lock,
      });
      this._draft = null;
    } catch (error) {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: `Could not set meal: ${error.message || error}` },
          bubbles: true,
          composed: true,
        })
      );
    } finally {
      this._update();
    }
  }

  async _toggleLock() {
    if (this._locked) {
      await this._hass.callService("input_boolean", "turn_off", {
        entity_id: this._entities().lock,
      });
      return;
    }
    await this._setMeal(true);
  }

  async _openMeal() {
    if (this._open.disabled) return;
    try {
      await this._hass.callService("script", "kitchen_open_meal", {
        slot: Number(this.config.slot),
      });
    } catch (error) {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: `Could not open meal: ${error.message || error}` },
          bubbles: true,
          composed: true,
        })
      );
    }
  }
}

class KitchenMealActionCard extends HTMLElement {
  setConfig(config) {
    if (!config?.entity?.startsWith("script.") || !config.name || !config.icon) {
      throw new Error("Kitchen meal action requires a script entity, name, and icon");
    }
    this.config = config;
    this._pending = false;
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 1;
  }

  _build() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { height: 100%; overflow: hidden; }
        button {
          box-sizing: border-box; width: 100%; min-height: 92px; border: 0;
          display: flex; flex-direction: column; align-items: center;
          justify-content: center; gap: 8px; padding: 14px;
          background: transparent; color: var(--primary-text-color);
          font: inherit; font-weight: 500; cursor: pointer;
        }
        button:disabled {
          background: var(--secondary-background-color);
          color: var(--secondary-text-color); cursor: wait; opacity: .72;
        }
        ha-icon { color: var(--primary-color); --mdc-icon-size: 40px; }
        .spinner {
          width: 24px; height: 24px; border-radius: 50%;
          border: 3px solid var(--divider-color);
          border-top-color: var(--primary-color);
          animation: spin .8s linear infinite;
        }
        [hidden] { display: none; }
        @keyframes spin { to { transform: rotate(360deg); } }
      </style>
      <ha-card>
        <button type="button">
          <ha-icon></ha-icon>
          <span class="spinner" hidden></span>
          <span class="label"></span>
        </button>
      </ha-card>`;
    this._button = this.shadowRoot.querySelector("button");
    this._icon = this.shadowRoot.querySelector("ha-icon");
    this._spinner = this.shadowRoot.querySelector(".spinner");
    this._label = this.shadowRoot.querySelector(".label");
    this._button.addEventListener("click", () => this._run());
    this._render();
  }

  _render() {
    if (!this._button) return;
    const running = this._hass?.states[this.config.entity]?.state === "on";
    const busy = this._pending || running;
    this._button.disabled = busy;
    this._button.setAttribute("aria-busy", String(busy));
    this._icon.setAttribute("icon", this.config.icon);
    this._icon.hidden = busy;
    this._spinner.hidden = !busy;
    this._label.textContent = busy
      ? this.config.busy_name || "Processing…"
      : this.config.name;
  }

  async _run() {
    if (!this._hass || this._button.disabled) return;
    if (
      this.config.confirmation &&
      !window.confirm(this.config.confirmation)
    ) return;
    const service = this.config.entity.slice("script.".length);
    this._pending = true;
    this._render();
    try {
      await this._hass.callService("script", service);
    } catch (error) {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: `Action failed: ${error.message || error}` },
          bubbles: true,
          composed: true,
        })
      );
    } finally {
      this._pending = false;
      this._render();
    }
  }
}

if (!customElements.get("kitchen-meal-picker-card")) {
  customElements.define("kitchen-meal-picker-card", KitchenMealPickerCard);
}
if (!customElements.get("kitchen-meal-action-card")) {
  customElements.define("kitchen-meal-action-card", KitchenMealActionCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "kitchen-meal-picker-card",
  name: "Kitchen Meal Picker",
  description: "Search Mealie recipes or enter and lock a custom meal.",
});
window.customCards.push({
  type: "kitchen-meal-action-card",
  name: "Kitchen Meal Action",
  description: "Run a meal-planner action with disabled and busy states.",
});
