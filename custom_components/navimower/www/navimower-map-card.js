/*
 * Navimower Map Card
 *
 * Renders private-cloud map geometry from the authenticated Navimower HTTP API
 * and overlays the live official-MQTT pose, heading, local channels and the
 * current mowing trail. No external JavaScript dependencies.
 *
 * Example:
 *   type: custom:navimower-map-card
 *   map_entity: sensor.tont_map_data
 *   x_entity: sensor.tont_position_x
 *   y_entity: sensor.tont_position_y
 *   heading_entity: sensor.tont_heading
 *   status_entity: lawn_mower.tont
 *   battery_entity: sensor.tont_battery
 *   zone_entity: sensor.tont_current_zone
 */
class NavimowerMapCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.map_entity) {
      throw new Error("navimower-map-card: `map_entity` is required");
    }
    this._config = Object.assign({
      title: "Navimower Map",
      x_entity: null,
      y_entity: null,
      heading_entity: null,
      status_entity: null,
      battery_entity: null,
      zone_entity: null,
      trail_length: 10000,
      show_zone_labels: true,
      show_channels: true,
      show_tunnels: true,
      show_legend: true,
    }, config);

    this._mapPayload = null;
    this._mapKey = null;
    this._loadingMap = false;
    this._loadError = null;
    this._failedMapKey = null;
    this._retryAfter = 0;
    this._trail = [];
    this._lastPointKey = null;
    this._previousStatus = null;

    this.innerHTML = `
      <ha-card>
        <div class="nm-title"></div>
        <div class="nm-wrap">
          <svg class="nm-map" viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid meet"></svg>
        </div>
        <div class="nm-footer"></div>
      </ha-card>
      <style>
        ha-card { padding: 12px; }
        .nm-title { font-size: 1.05rem; font-weight: 600; margin: 0 2px 8px; }
        .nm-wrap { position: relative; width: 100%; aspect-ratio: 1 / 1;
          overflow: hidden; border-radius: 10px; background: var(--secondary-background-color); }
        .nm-map { width: 100%; height: 100%; display: block; touch-action: none; }
        .nm-footer { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 8px 2px 0;
          color: var(--secondary-text-color); font-size: .9rem; }
        .nm-footer b { color: var(--primary-text-color); }
      </style>`;
  }

  set hass(hass) {
    this._hass = hass;
    this._maybeLoadMap();
    this._updateLive();
  }

  static getStubConfig() {
    return {
      map_entity: "sensor.navimower_map_data",
      x_entity: "sensor.navimower_position_x",
      y_entity: "sensor.navimower_position_y",
      heading_entity: "sensor.navimower_heading",
      status_entity: "lawn_mower.navimower",
      battery_entity: "sensor.navimower_battery",
      zone_entity: "sensor.navimower_current_zone",
    };
  }

  getCardSize() { return 7; }

  _state(entityId) {
    return entityId && this._hass ? this._hass.states[entityId] : null;
  }

  _number(entityId) {
    const state = this._state(entityId);
    if (!state || ["unknown", "unavailable", "none", ""].includes(state.state)) return null;
    const value = Number(state.state);
    return Number.isFinite(value) ? value : null;
  }

  _text(entityId, fallback = "—") {
    const state = this._state(entityId);
    return state && !["unknown", "unavailable", "none", ""].includes(state.state)
      ? state.state : fallback;
  }

  async _maybeLoadMap() {
    const entity = this._state(this._config.map_entity);
    if (!entity) {
      this._loadError = `Map entity not found: ${this._config.map_entity}`;
      this._render();
      return;
    }
    const attrs = entity.attributes || {};
    const apiPath = attrs.api_path;
    if (!apiPath) {
      this._loadError = "Map entity has no api_path attribute";
      this._render();
      return;
    }
    const key = [apiPath, attrs.map_version, attrs.map_modified_count, entity.state].join("|");
    if (key === this._mapKey || this._loadingMap) return;
    if (key === this._failedMapKey && Date.now() < this._retryAfter) return;

    this._loadingMap = true;
    this._loadError = null;
    try {
      const path = String(apiPath).replace(/^\/api\//, "").replace(/^\/+/, "");
      const payload = await this._hass.callApi("GET", path);
      this._mapPayload = payload || {};
      this._mapKey = key;
      this._failedMapKey = null;
      this._retryAfter = 0;
      if (!this._trail.length && Array.isArray(payload?.trail)) {
        this._trail = payload.trail
          .filter((point) => Array.isArray(point) && point.length >= 2)
          .map((point) => [Number(point[0]), Number(point[1])])
          .filter((point) => point.every(Number.isFinite));
        this._trimTrail();
      }
    } catch (err) {
      this._loadError = `Map load failed: ${err?.message || err}`;
      this._failedMapKey = key;
      this._retryAfter = Date.now() + 30000;
    } finally {
      this._loadingMap = false;
      this._render();
    }
  }

  _updateLive() {
    if (!this._config || !this._hass) return;
    const x = this._number(this._config.x_entity);
    const y = this._number(this._config.y_entity);
    const status = this._text(this._config.status_entity, "unknown");

    if (status === "mowing" && ["docked", "idle"].includes(this._previousStatus)) {
      this._trail = [];
      this._lastPointKey = null;
    }
    this._previousStatus = status;

    if (status === "mowing" && x !== null && y !== null) {
      const key = `${x.toFixed(3)},${y.toFixed(3)}`;
      if (key !== this._lastPointKey) {
        const previous = this._trail[this._trail.length - 1];
        if (!previous || Math.hypot(x - previous[0], y - previous[1]) >= 0.12) {
          this._trail.push([x, y]);
          this._trimTrail();
        }
        this._lastPointKey = key;
      }
    }
    this._render();
  }

  _trimTrail() {
    const cap = Math.max(100, Number(this._config.trail_length) || 10000);
    while (this._trail.length > cap) {
      const last = this._trail[this._trail.length - 1];
      this._trail = this._trail.filter((_, index) => index % 2 === 0);
      if (this._trail[this._trail.length - 1] !== last) this._trail.push(last);
    }
  }

  _esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  _render() {
    if (!this._config) return;
    const title = this.querySelector(".nm-title");
    const svg = this.querySelector(".nm-map");
    const footer = this.querySelector(".nm-footer");
    if (!title || !svg || !footer) return;
    title.textContent = this._config.title;

    const status = this._text(this._config.status_entity);
    const zone = this._text(this._config.zone_entity);
    const battery = this._number(this._config.battery_entity);
    const x = this._number(this._config.x_entity);
    const y = this._number(this._config.y_entity);
    const heading = this._number(this._config.heading_entity);

    const footerParts = [`Status: <b>${this._esc(status)}</b>`];
    if (zone !== "—") footerParts.push(`Zone: <b>${this._esc(zone)}</b>`);
    if (battery !== null) footerParts.push(`Battery: <b>${battery.toFixed(0)}%</b>`);
    if (x !== null && y !== null) footerParts.push(`Position: <b>${x.toFixed(2)}, ${y.toFixed(2)} m</b>`);
    footer.innerHTML = footerParts.join("");

    if (this._loadError && !this._mapPayload) {
      svg.innerHTML = this._placeholder(this._loadError);
      return;
    }
    if (!this._mapPayload) {
      svg.innerHTML = this._placeholder(this._loadingMap ? "Loading map…" : "Waiting for map data…");
      return;
    }

    const map = this._mapPayload.map || {};
    const zones = Array.isArray(map.zones) ? map.zones : [];
    const obstacles = Array.isArray(map.obstacles) ? map.obstacles : [];
    const noMow = Array.isArray(map.vision_off) ? map.vision_off : [];
    const tunnels = Array.isArray(map.tunnels) ? map.tunnels : [];
    const channels = Array.isArray(this._mapPayload.channels) ? this._mapPayload.channels : [];
    const station = map.station || null;

    const stable = [];
    zones.forEach((z) => (z.polygon || []).forEach((p) => stable.push(p)));
    obstacles.forEach((poly) => poly.forEach((p) => stable.push(p)));
    noMow.forEach((poly) => poly.forEach((p) => stable.push(p)));
    tunnels.forEach((t) => (t.points || []).forEach((p) => stable.push(p)));
    if (this._config.show_channels) {
      channels.forEach((c) => {
        stable.push([c.x_min, c.y_min], [c.x_min, c.y_max], [c.x_max, c.y_min], [c.x_max, c.y_max]);
      });
    }
    if (station && Number.isFinite(Number(station.x)) && Number.isFinite(Number(station.y))) {
      stable.push([Number(station.x), Number(station.y)]);
    }

    let points = stable.slice();
    const dynamic = this._trail.slice();
    if (x !== null && y !== null) dynamic.push([x, y]);
    if (stable.length) {
      const bx = stable.map((p) => Number(p[0])).filter(Number.isFinite);
      const by = stable.map((p) => Number(p[1])).filter(Number.isFinite);
      const minX = Math.min(...bx), maxX = Math.max(...bx);
      const minY = Math.min(...by), maxY = Math.max(...by);
      const margin = Math.max(maxX - minX, maxY - minY) * 0.15 + 1;
      dynamic.forEach((p) => {
        if (p[0] >= minX - margin && p[0] <= maxX + margin && p[1] >= minY - margin && p[1] <= maxY + margin) points.push(p);
      });
    } else {
      points = dynamic;
    }
    if (!points.length) {
      svg.innerHTML = this._placeholder("Map geometry is empty");
      return;
    }

    const pxs = points.map((p) => Number(p[0])).filter(Number.isFinite);
    const pys = points.map((p) => Number(p[1])).filter(Number.isFinite);
    let minX = Math.min(...pxs), maxX = Math.max(...pxs);
    let minY = Math.min(...pys), maxY = Math.max(...pys);
    let spanX = Math.max(maxX - minX, 0.1);
    let spanY = Math.max(maxY - minY, 0.1);
    minX -= spanX * 0.05; maxX += spanX * 0.05;
    minY -= spanY * 0.05; maxY += spanY * 0.05;
    spanX = maxX - minX; spanY = maxY - minY;

    const V = 1000;
    const scale = Math.min(V / spanX, V / spanY);
    const offsetX = (V - spanX * scale) / 2;
    const offsetY = (V - spanY * scale) / 2;
    const sx = (wx) => offsetX + (wx - minX) * scale;
    const sy = (wy) => offsetY + (maxY - wy) * scale;
    const ptString = (poly) => poly.map((p) => `${sx(Number(p[0])).toFixed(1)},${sy(Number(p[1])).toFixed(1)}`).join(" ");

    const coverage = new Map();
    for (const item of this._mapPayload.coverage?.zones || []) {
      coverage.set(Number(item.id), item.pct);
    }

    const out = [];
    out.push(`<rect width="${V}" height="${V}" fill="var(--secondary-background-color)"/>`);

    const labels = [];
    for (const zoneItem of zones) {
      const poly = zoneItem.polygon || [];
      if (poly.length < 3) continue;
      out.push(`<polygon points="${ptString(poly)}" fill="#81c784" fill-opacity="0.22" stroke="none"/>`);
      out.push(this._perimeter(poly, zoneItem.boundary_flags || [], sx, sy));
      if (this._config.show_zone_labels) {
        const cx = poly.reduce((sum, p) => sum + sx(Number(p[0])), 0) / poly.length;
        const cy = poly.reduce((sum, p) => sum + sy(Number(p[1])), 0) / poly.length;
        const pct = coverage.get(Number(zoneItem.id));
        const label = pct === undefined || pct === null
          ? (zoneItem.name || `Zone ${zoneItem.id}`)
          : `${zoneItem.name || `Zone ${zoneItem.id}`} · ${pct}%`;
        labels.push([cx, cy, label]);
      }
    }

    obstacles.forEach((poly) => {
      if (poly.length >= 3) out.push(`<polygon points="${ptString(poly)}" fill="#616161" fill-opacity="0.72" stroke="#424242" stroke-width="2"/>`);
    });
    noMow.forEach((poly) => {
      if (poly.length >= 3) out.push(`<polygon points="${ptString(poly)}" fill="#bdbdbd" fill-opacity="0.34" stroke="#757575" stroke-width="2" stroke-dasharray="9 6"/>`);
    });

    if (this._config.show_tunnels) {
      tunnels.forEach((tunnel) => {
        const pts = tunnel.points || [];
        if (pts.length >= 2) out.push(`<polyline points="${ptString(pts)}" fill="none" stroke="#039be5" stroke-width="${Math.max(4, scale * .35).toFixed(1)}" stroke-opacity=".48" stroke-linecap="round" stroke-dasharray="12 8"/>`);
      });
    }

    if (this._config.show_channels) {
      channels.forEach((channel) => {
        const x1 = sx(Number(channel.x_min));
        const x2 = sx(Number(channel.x_max));
        const y1 = sy(Number(channel.y_max));
        const y2 = sy(Number(channel.y_min));
        out.push(`<rect x="${Math.min(x1, x2).toFixed(1)}" y="${Math.min(y1, y2).toFixed(1)}" width="${Math.abs(x2 - x1).toFixed(1)}" height="${Math.abs(y2 - y1).toFixed(1)}" fill="#ab47bc" fill-opacity=".14" stroke="#8e24aa" stroke-width="3" stroke-dasharray="10 6"/>`);
        const labelX = (x1 + x2) / 2;
        const labelY = Math.min(y1, y2) + 22;
        out.push(this._label(labelX, labelY, channel.name || "Channel", 18));
      });
    }

    if (this._trail.length >= 2) {
      const breakSquared = 25;
      const segments = [[]];
      let previous = null;
      for (const point of this._trail) {
        if (previous && (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2 > breakSquared) segments.push([]);
        segments[segments.length - 1].push(point);
        previous = point;
      }
      const width = Math.min(Math.max(.25 * scale, 5), 28);
      const trails = segments
        .filter((segment) => segment.length >= 2)
        .map((segment) => `<polyline points="${ptString(segment)}" fill="none" stroke="#43a047" stroke-width="${width.toFixed(1)}" stroke-linecap="round" stroke-linejoin="round"/>`)
        .join("");
      if (trails) out.push(`<g opacity=".40">${trails}</g>`);
    }

    if (station && Number.isFinite(Number(station.x)) && Number.isFinite(Number(station.y))) {
      out.push(this._station(sx(Number(station.x)), sy(Number(station.y))));
    }
    if (x !== null && y !== null) out.push(this._mower(sx(x), sy(y), heading));
    labels.forEach(([cx, cy, label]) => out.push(this._pill(cx, cy, label)));
    if (this._config.show_legend) out.push(this._legend(channels.length > 0, tunnels.length > 0));
    if (this._loadError) out.push(this._label(V / 2, V - 18, this._loadError, 16));
    svg.innerHTML = out.join("");
  }

  _perimeter(poly, flags, sx, sy) {
    const count = poly.length;
    if (count < 2) return "";
    const pairs = [];
    for (let index = 0; index < count - 1; index += 1) pairs.push([index, index + 1]);
    if (poly[0][0] !== poly[count - 1][0] || poly[0][1] !== poly[count - 1][1]) pairs.push([count - 1, 0]);
    return pairs.map(([a, b]) => {
      const solid = Number(flags[a]) === 2;
      const p1 = poly[a], p2 = poly[b];
      return `<line x1="${sx(Number(p1[0])).toFixed(1)}" y1="${sy(Number(p1[1])).toFixed(1)}" x2="${sx(Number(p2[0])).toFixed(1)}" y2="${sy(Number(p2[1])).toFixed(1)}" stroke="#43a047" stroke-width="3" stroke-linecap="round"${solid ? "" : ' stroke-dasharray="10 7"'}/>`;
    }).join("");
  }

  _mower(cx, cy, headingDegrees) {
    const degrees = Number.isFinite(headingDegrees) ? -headingDegrees : 0;
    return `<g transform="translate(${cx.toFixed(1)},${cy.toFixed(1)}) rotate(${degrees.toFixed(1)})">
      <rect x="-23" y="-18" width="46" height="36" rx="12" fill="#263238" stroke="#fff" stroke-width="4"/>
      <circle cx="-8" cy="-8" r="3.5" fill="#eceff1"/><circle cx="-8" cy="8" r="3.5" fill="#eceff1"/>
      <circle cx="14" cy="0" r="8" fill="#ff6d00" stroke="#fff" stroke-width="2"/>
    </g>`;
  }

  _station(cx, cy) {
    return `<g transform="translate(${cx.toFixed(1)},${cy.toFixed(1)})">
      <rect x="-17" y="-14" width="34" height="28" rx="6" fill="#37474f" stroke="#fff" stroke-width="3"/>
      <path d="M3 -10 L-6 1 H0 L-3 10 L7 -2 H1 Z" fill="#69f0ae"/>
    </g>`;
  }

  _pill(cx, cy, value) {
    const text = this._esc(value);
    const width = Math.max(90, String(value).length * 10 + 24);
    return `<g><rect x="${(cx - width / 2).toFixed(1)}" y="${(cy - 18).toFixed(1)}" width="${width.toFixed(1)}" height="36" rx="18" fill="#eceff1" fill-opacity=".94" stroke="#b0bec5" stroke-width="1.5"/>
      <text x="${cx.toFixed(1)}" y="${(cy + 6).toFixed(1)}" text-anchor="middle" font-family="sans-serif" font-size="18" font-weight="600" fill="#37474f">${text}</text></g>`;
  }

  _label(cx, cy, value, size = 17) {
    return `<text x="${cx.toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="middle" font-family="sans-serif" font-size="${size}" font-weight="600" paint-order="stroke" stroke="#fff" stroke-width="4" fill="#263238">${this._esc(value)}</text>`;
  }

  _legend(hasChannels, hasTunnels) {
    const rows = [["#ff6d00", "Mower"], ["#43a047", "Mowed"], ["#455a64", "Dock"], ["#616161", "Obstacle"], ["#bdbdbd", "No-mow"]];
    if (hasChannels) rows.push(["#8e24aa", "Channel"]);
    if (hasTunnels) rows.push(["#039be5", "Tunnel"]);
    const height = rows.length * 28 + 18;
    let result = `<g><rect x="14" y="14" width="150" height="${height}" rx="9" fill="#fff" fill-opacity=".82" stroke="#9e9e9e" stroke-opacity=".35"/>`;
    rows.forEach(([color, name], index) => {
      const y = 38 + index * 28;
      result += `<rect x="27" y="${y - 13}" width="18" height="18" rx="3" fill="${color}"/><text x="55" y="${y + 1}" font-family="sans-serif" font-size="18" font-weight="600" fill="#263238">${name}</text>`;
    });
    return `${result}</g>`;
  }

  _placeholder(message) {
    return `<rect width="1000" height="1000" fill="var(--secondary-background-color)"/><text x="500" y="500" text-anchor="middle" font-family="sans-serif" font-size="30" fill="var(--secondary-text-color)">${this._esc(message)}</text>`;
  }
}

if (!customElements.get("navimower-map-card")) {
  customElements.define("navimower-map-card", NavimowerMapCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "navimower-map-card",
  name: "Navimower Map",
  description: "Private Navimow map with MQTT live position, trail and local channels.",
});
