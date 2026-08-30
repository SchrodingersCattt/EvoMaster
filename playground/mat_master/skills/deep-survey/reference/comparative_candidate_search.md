# Comparative candidate search

This contract governs an open candidate-discovery workflow. Its numerical settings and evidence roles are supplied by the run configuration.

Complete all configured broad-search facets before issuing a search for a named option.

Broad-search queries must not name, list, or exemplify any candidate option.

Build a longlist from the broad-search results, then write the configured number of finalists to the finalists array in run_result.json. Do this before any targeted retrieval. The remaining result fields may be provisional at this stage. The runtime locks these model-selected identifiers on the first targeted query, and the list must not change afterward.

After all configured broad-search facets, prefix any optional additional candidate-neutral broad query with [BROAD]. Do not use this prefix for targeted retrieval.

Execute targeted retrieval in synchronized rounds. Each round covers every finalist once under the same evidence role, retrieval parameters, and result limit.

Include exactly one locked finalist identifier in every targeted retrieval query.

Never combine multiple finalists in one targeted query, including gap-filling queries.

Do not deepen one finalist alone. Any additional retrieval round must be applied to every finalist.

Inspect the same number of top-ranked records for every finalist. Record an unsuccessful search explicitly when no eligible source is found.

Tag every inspected record with its locked finalist and the configured evidence-role text.

Keep source observations, adverse findings, inferred explanations, and unresolved gaps separate.

Do not create a favorable range, threshold, mechanism, or performance claim that is absent from the inspected evidence.

Complete the configured evidence matrix before making the requested decision. Abstention is permitted.

Save the inspected-source records and final artifacts before requesting finish. The runtime records and audits protocol state.
