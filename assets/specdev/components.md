# Component & Interface Map — <FEATURE NAME>

Derived from the architecture decision. Each component is an isolated unit a
build subagent can own end-to-end. Define the **contract** (inputs → outputs)
so parallel agents don't drift into each other's boundaries.

| Component | Owns (REQs) | Contract / Interface | Depends on |
|-----------|-------------|----------------------|------------|
| <name>    | REQ-001     | <input → output>     | <component / external> |
|           |             |                      |            |

## Notes

- For **extensions**, mark components as `new`, `modified`, or `untouched` so
  the blast radius is explicit and characterization tests target `modified` ones.
