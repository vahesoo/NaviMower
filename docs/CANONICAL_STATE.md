# Canonical Mower State migration

Navimower 0.5 starts a deliberate internal architecture migration. The goal is not to rewrite working vendor integrations; it is to give each semantic concept one owner and remove the historical stack of post-processing layers.

Target flow:

    private cloud ----\
                       -> observations -> Canonical Mower State
    official MQTT ----/                         |
                                      +---------+---------+
                                      |                   |
                                  Cycle Engine          History
                                      |                   |
                                      +---------+---------+
                                                |
                                          render resources
                                                |
                                             Map Card

The backend remains authoritative for vendor semantics, cycle identity, georeference, Schedule, Gate logic, History and prepared render resources. The Map Card remains presentation and interaction.

## Beta 1

0.5.0-beta1 introduced the first **shadow-only** canonical reducer and field parity diagnostics.

The new reducer proved these boundaries without replacing public state:

- position source arbitration is one resolver;
- task state is projected once;
- ZoneLedger is projected into a CycleEngine-shaped per-zone model;
- VendorTrailStore ownership is attached to those cycles instead of being inferred in the frontend;
- diagnostics compare the candidate with the public runtime.

The sparse-MQTT H1 regression is a required case: private-cloud position may take over when MQTT pose is stale, while vendor coverage/current-cycle geometry remain independent.

No entity or service reads Canonical v2 as its authority in beta1.

## Beta 2

0.5.0-beta2 deliberately remains shadow-only after the first field pass exposed
two migration-specific gaps:

- X390 retained same-cycle progress needed a stronger vendor-start identity rule
  plus a one-way History peak migration seed;
- H215 task area is a canonical enrichment when legacy does not publish a value,
  not a compatibility failure.

Beta2 also makes sparse/missing/stale MQTT pose fallback reasons explicit in
canonical diagnostics. Public entities and the existing Map API remain on the
legacy runtime while these fixes are verified on real mowers.

## Beta 3

0.5.0-beta3 remains shadow-only for Canonical v2 but intentionally aligns the
existing current-map publication with fresh vendor state before authority
cutover.

- fresh vendor per-zone percentage is the visible current percentage, including
  vendor resets back to 0%;
- historical completion remains History metadata and does not repaint the
  current map;
- VendorTrailStore current geometry is revoked when the vendor resets that zone;
- reset checkpoints prevent lagging compressed geometry from resurrecting an
  old prefix;
- current mowed fallback geometry is cutting-only;
- docked/charging retained sessions do not define a current task zone set.

The matching Map Card 0.4.0-beta3 also renders semantic live **cutting** segments
only in the Mowed layer. Travel/return data may still exist in the backend
session model, but it is not current mowing geometry.

The next authority/API cutover is conditional on field validation of these
vendor-current semantics.


## Beta 4

0.5.0-beta4 is an intentionally narrow field fix inserted before cutover. It
keeps existing retained vendor geometry untouched and applies a forward-only
5 m gap guard to newly appended vendor trail edges. The guard is independent of
Canonical ownership and remains active after cutover.

## Beta 5 — authority cutover

0.5.0-beta5 promotes the new architecture from verification to public
authority:

- Canonical Mower State owns resolved position/navigation/task projection;
- ZoneLedger / Cycle Engine publishes current per-zone progress and map/task
  totals;
- VendorTrailStore owns current-cycle vendor geometry;
- History owns completed/retained session archive data;
- Map API advertises paired resource contract v2 and a compact canonical block;
- Map Card 0.4.0-beta5 prefers the canonical cycle rows for zone progress;
- existing top-level Map API aliases remain temporarily during this cutover beta
  but are produced from the same backend authority.

Schedule, Gate, weather, georeference and command implementations retain their
dedicated backend owners. They are not moved into the frontend.

The next beta is a **cleanup/consolidation beta**: remove superseded semantic
wrappers, shadow-only code and legacy Map API/render aliases, and modularize Map
Card source while keeping one deterministic production bundle. New feature work
in migrated domains should extend the owning resolver/module rather than add a
new wrapper chain.
