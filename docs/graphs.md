# Alfred's graphs

`.omni/graphs/` — JSON documents run by omni-kernel's graph walker (graph-spec). Every
node declares its own context (`inputs`); nothing else reaches it.

Naming rule (J8): a node **speaks to the user** iff it is `converse: true` or its id
starts with `say-`.

## front-door

> **Planned (omni-jev, A3).** The conversation graph. `front` (Jev) sorts the request into
> device / troubleshoot / routine / new-capability / chat; `device-action` (Jev) picks the
> verb; `resolve`/`resolve-read` resolve the entity (Jev inside the HA server);
> `say-act`/`say-status` act and answer without a chat model; `device-help`, `chat` and
> `clarify` are converse agents; three graph nodes run the subgraphs. Full JSON:
> [omni-jev-plan.md](omni-jev-plan.md) §5.2.

## device-troubleshoot

Today: `diagnose` → `recover` → `confirm-fixed` → `report` | `escalate`, inputs `device`
and `symptom`.

> **Planned (omni-jev, A3).** Inputs `report` and `earlier`. `intake` (converse) finds the
> device and symptom with the person; `inspect` gathers evidence; `triage` (Jev) chooses
> recoverable / healthy / needs-human with `diagnose-llm` as the `unsure` fallback;
> `recover` → `read-back` → `fixed` (Jev, `confirm-llm` fallback); `say-report`,
> `say-escalate`, `say-bye` speak. Full JSON: plan §5.3.

## home-routine

Today: `plan` → `confirm` (human) → `apply` → `verify` → `report`; `cancelled`.

> **Planned (omni-jev, A3).** `verify` becomes `read-back` + `settled` (Jev) with a
> `verify-llm` fallback; `report`/`cancelled` become `say-report`/`say-cancelled`. Plan §5.4.

## new-capability

Today: `design` → `approve` (human) → `build` → `verify` → `review` (human) →
`installed`; `declined`.

> **Planned (omni-jev, A3).** Renames only: `say-installed`, `say-declined` (J20). Plan §5.5.
