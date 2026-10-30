# Relation audit — adjudication of inheritance/implementation edges

Method: script re-check: depth-aware header parser + transitive closure. No human review is claimed here; the seeded worksheet in `relation_audit_human_spotcheck.csv` carries the pending human spot-check.

## archived

- inheritance edges: 1314
- automated screen: 999 verified / 1106 checkable = 90.3%
- adjudicated: 1063 confirmed / 1088 with both endpoints resolvable = 97.7%
- endpoints not resolvable in the pinned sources (outside the method): 226

| verdict | edges |
|---|---:|
| verified_confirmed | 999 |
| source_not_declared | 171 |
| external_target | 42 |
| artifact_direct | 35 |
| artifact_from_unresolved | 24 |
| direction_reversed | 15 |
| resolved_external_target | 13 |
| confirmed_absent | 10 |
| artifact_transitive | 4 |
| artifact_qualified | 1 |

## mindustry-block

- inheritance edges: 674
- automated screen: 645 verified / 655 checkable = 98.5%
- adjudicated: 667 confirmed / 672 with both endpoints resolvable = 99.3%
- endpoints not resolvable in the pinned sources (outside the method): 2

| verdict | edges |
|---|---:|
| verified_confirmed | 645 |
| artifact_from_unresolved | 19 |
| confirmed_absent | 5 |
| artifact_transitive | 3 |
| external_target | 2 |
