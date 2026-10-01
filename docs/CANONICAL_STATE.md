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

0.5.0-beta1 is intentionally **shadow-only**.

The new reducer currently proves these boundaries without replacing public state:

- position source arbitration is one resolver;
- task state is projected once;
- ZoneLedger is projected into a CycleEngine-shaped per-zone model;
- VendorTrailStore ownership is attached to those cycles instead of being inferred in the frontend;
- diagnostics compare the candidate with the public runtime.

The sparse-MQTT H1 regression is a required case: private-cloud position may take over when MQTT pose is stale, while vendor coverage/current-cycle geometry remain independent.

No entity or service reads Canonical v2 as its authority in beta1.

## Planned next steps

- beta2: move public consumers to Canonical Mower State / Cycle Engine and introduce the paired Map API v2 contract;
- beta3: delete superseded wrappers, legacy Map API/render fallbacks and split Map Card source into maintainable modules while keeping one production bundle.
