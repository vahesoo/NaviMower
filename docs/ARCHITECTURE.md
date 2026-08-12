# Navimower production architecture

## Runtime naming rule

Release and beta numbers belong in `manifest.json`, changelog entries, release notes, Git tags and historical tests. They do **not** belong in production Python module names or runtime installer symbols.

Production behavior must be grouped by responsibility. The current semantic extension boundary is:

- `state_semantics.py` — proven state/error interpretation and error-sensor enrichment;
- `private_cloud_region.py` — persistence/diagnostics bridge for regional private-cloud routing owned by `api/regions.py` and the API client;
- `capability_extensions.py` — model capabilities, dynamic limits and route-history compaction;
- `capability_profile.py` — evidence-first observed capability inventory for diagnostics and future entity provisioning;
- `navigation_fallback.py` — freshness-aware MQTT/private-cloud navigation fallback and gate safety;
- `notification_feed.py` — vendor notification transport/cache plus merged notification snapshot decoration;
- `runtime.py` — the single ordered composition point for those extensions.

`services.py` may call only the central `install_runtime_extensions()` composition point. Semantic modules must not chain-install one another.

## Capability policy

Capability discovery is **positive-evidence first**. A field, endpoint or MQTT feature that has been observed may be retained as supported for the lifetime of the integration instance. One missing or empty response is not proof that a mower lacks the feature; transient cloud failures and idle-state payloads must not remove entities.

Model-family rules are allowed only for narrow constraints that are already proven, such as first-generation H-series mowers not supporting user-defined ordered zone sequences. Unknown models must not be assigned capabilities merely because their model name shares a prefix with a known mower.

The capability profile is diagnostic/foundational. It does not prune the general sensor platform. Future capability-driven provisioning must preserve the same confirmed-data rule before removing a registry entity.

## Regional connection boundary

The mobile-app private cloud and the official Smart Home connection are separate transports. Private-cloud account ownership is resolved across regional passport services and the usable private mower host is persisted per config entry. Smart Home OAuth/API routing is not inferred from that private region; MQTT continues to use `mqttHost`/`mqttUrl` returned by the official API.

## Rule for future betas

Do not add `betaNN_runtime.py`, `vNN_runtime.py`, `install_betaNN_*`, or `_betaNN_*` production symbols as a cache/workaround or release mechanism. A beta is cumulative: changes go directly into the current semantic production modules and the latest beta is already the stable-release candidate.

If a future protocol experiment genuinely needs temporary isolation, give it a responsibility-based name such as `experimental_<feature>.py`. The same change must document why isolation is needed and explicitly update the architecture guard in `tests/test_runtime_architecture.py`. Removing the guard or adding a blanket beta-number exception is not an acceptable workaround.

Historical beta behavior remains recoverable from Git tags and versioned tests/release notes; production source does not need to carry old release layers.
