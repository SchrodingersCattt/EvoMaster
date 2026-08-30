# Comparative candidate search

This contract governs an open candidate-discovery workflow. Its numerical settings and evidence roles are supplied by the run configuration.

Complete all configured broad-search facets before issuing a search for a named option.

Build a longlist from the broad-search results, then lock the configured number of finalists. Do not change the finalist set after targeted retrieval begins.

Execute targeted retrieval in synchronized rounds. Each round covers every finalist once under the same evidence role, retrieval parameters, and result limit.

Include exactly one locked finalist identifier in every targeted retrieval query.

Do not deepen one finalist alone. Any additional retrieval round must be applied to every finalist.

Inspect the same number of top-ranked records for every finalist. Record an unsuccessful search explicitly when no eligible source is found.

Tag every inspected record with its locked finalist and the configured evidence-role text.

Keep source observations, adverse findings, inferred explanations, and unresolved gaps separate.

Do not create a favorable range, threshold, mechanism, or performance claim that is absent from the inspected evidence.

Complete the configured evidence matrix before making the requested decision. Abstention is permitted.

Save the inspected-source records and final artifacts before requesting finish. The runtime records and audits protocol state.
