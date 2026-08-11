# Navimower production architecture

## Runtime naming rule

Release and beta numbers belong in `manifest.json`, changelog entries, release notes, Git tags and historical tests. They do **not** belong in production Python module names or runtime installer symbols.

Production behavior must be grouped by responsibility. The current semantic extension boundary is:

- `state_semantics.py` — proven state/error interpretation and error-sensor enrichment;
- `capability_extensions.py` — model capabilities, dynamic limits and route-history compaction;
- `navigation_fallback.py` — freshness-aware MQTT/private-cloud navigation fallback and gate safety;
- `notification_feed.py` — vendor notification transport/cache plus merged notification snapshot decoration;
- `runtime.py` — the single ordered composition point for those extensions.

`services.py` may call only the central `install_runtime_extensions()` composition point. Semantic modules must not chain-install one another.

## Rule for future betas

Do not add `betaNN_runtime.py`, `vNN_runtime.py`, `install_betaNN_*`, or `_betaNN_*` production symbols as a cache/workaround or release mechanism. A beta is cumulative: changes go directly into the current semantic production modules and the latest beta is already the stable-release candidate.

If a future protocol experiment genuinely needs temporary isolation, give it a responsibility-based name such as `experimental_<feature>.py`. The same change must document why isolation is needed and explicitly update the architecture guard in `tests/test_runtime_architecture.py`. Removing the guard or adding a blanket beta-number exception is not an acceptable workaround.

Historical beta behavior remains recoverable from Git tags and versioned tests/release notes; production source does not need to carry old release layers.
