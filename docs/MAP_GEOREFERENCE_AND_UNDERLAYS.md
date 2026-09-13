# Map georeference and underlays

Navimower keeps the mower's own local X/Y map geometry as the source of truth for zones, the dock, Off-limit/VF-off areas, Channels, Gate areas, Custom Areas and dense mower position. Geographic map underlays are an additional presentation layer; they must not move mower-local geometry relative to itself.

## Georeference model

The integration builds and validates a local-map -> geographic transform from the best evidence available for each map. The exact evidence can differ by mower generation and firmware. Current production paths include:

- explicit vendor geographic transform metadata where the map provides it;
- internally consistent static vendor map anchors/tie points;
- X3-family RTK anchor/bias metadata when the required calibration evidence is present;
- a learned private-cloud local-X/Y + GPS fit used as a model-independent source, validation signal and, on eligible static maps, translation-only refinement.

A map-revision change is the authoritative automatic boundary for rebuilding map calibration. Ordinary GPS drift must not continuously move an already validated map. The learned fit uses spatially separated samples, rejects poor/outlier fits and prefers wider two-dimensional coverage rather than a rolling set of the newest points.

Some firmwares may temporarily report a local `(0, 0, 0)` pose while docked while retaining an older geographic point. Navimower treats a proven inconsistent zero-pose pair as a vendor placeholder instead of feeding it into georeference learning.

The native Home Assistant **Location** `device_tracker` is separate from map georeferencing: it reports the vendor private-cloud GPS position directly. Dense MQTT X/Y remains mower-local Cartesian data.

## Provider frames

The authenticated Map/Site API can expose provider-ready georeference frames while keeping all mower-local geometry in one stable frame. The current contract distinguishes:

- `active` — the integration's active geographic presentation frame;
- `web_wgs84` — the web-map/WGS84 provider frame;
- `regional_cartographic` — a regional cartographic presentation frame when supported.

Provider-specific frame selection is done server-side so changing the underlay does not require frontend model-specific offsets. In Multi mower view, provider-specific site origins move the whole site consistently instead of moving each mower independently.

European static orthophoto alignment may use the integration's cartographic-frame correction when the map has qualifying evidence. This translation is a presentation-layer correction only: local X/Y, map rotation, scale and the mower's position relative to its own map remain unchanged.

## Map underlay configuration

Open:

**Settings -> Devices & services -> Navimower -> Configure -> Map underlay**

The integration currently provides backend capability metadata for:

- **Estonia orthophoto** — available automatically for mower sites resolved to Estonia;
- **Estonia hybrid** — available automatically for mower sites resolved to Estonia;
- **Google Satellite** — optional and enabled by configuring a Google Map Tiles API key.

OpenStreetMap rendering does not require a Google key.

### Google Satellite

The Google Map Tiles API key is account-scoped. If several Navimower mower entries use the same private-cloud account, saving the key on one entry makes the same key available to the peer entries for that account.

A newly entered key is validated by creating a Google satellite tile session before it is saved. Leaving the password field empty keeps the current shared key; the Options Flow also provides an explicit clear option.

The API key and Google session token remain on the Home Assistant backend. The integration exposes authenticated tile/viewport proxy paths to the frontend and does not put either secret in the Map API payload. Google imagery is not prefetched or persistently stored by Navimower; tile responses are streamed and their cache headers are passed through.

## Diagnostics

Normal Home Assistant **Download diagnostics** contains the information needed to understand georeference health without exposing exact geographic coordinates. Useful fields include transform source/status, fit quality, sample/refinement state, local-frame comparisons, provider-frame availability and relative metre offsets.

Google diagnostics expose only configuration/session health such as configured state, session activity/expiry and generic last-error status. The API key and session token are not included.

For an explicit georeference field investigation, `navimower.export_raw_data` is intentionally different: it creates a sensitive local development capture with exact vendor/map data. Do not publish raw exports.

## Relearning a map transform

`navimower.relearn_georeference` clears only the selected mower's learned local-map -> WGS84 calibration and begins learning again from fresh samples. It does not clear map geometry, mowing history, Navimower Schedule state or other integration data.

Normally a manual relearn is unnecessary. Prefer it only when diagnosing a known calibration problem or after a controlled field test; a real map revision already causes the normal automatic recalibration path.

## Troubleshooting underlay alignment

When an underlay appears offset, first distinguish these cases:

1. **Mower vs its own zones/dock is wrong** — this is a local-map/pose problem, not a geographic underlay correction problem.
2. **Mower + zones + dock move together but the whole group is offset against imagery** — investigate georeference/provider-frame evidence.
3. **Only one underlay provider is offset** — compare provider frame/attribution behavior rather than adding a mower-model X/Y offset.
4. **Alignment changes after map editing** — check the new map revision and allow the integration to rebuild/refine its calibration.

Do not compensate by manually moving individual mower-local polygons. Navimower's design keeps local geometry fixed and corrects only the geographic presentation layer when evidence supports it.
