# Interview Problem Catalog

The bundled interview catalog is a small, versioned set of reviewed practice problems.
It is not a mirror of any external problem library.
Its source of truth is `src/openlearn/interview_problem_catalogs/openlearn-interview-v1.json`.

## Rights Policy

The first catalog revision supports two delivery types.

- `packaged` entries include content that openlearn may redistribute.
- `official_link` entries store only neutral metadata and an official HTTPS URL.

Every entry records its delivery type, rights basis, source URL, license, attribution, and permission note.
Packaged entries currently use only the `openlearn_original` rights basis and the repository's `AGPL-3.0-or-later` license.
The schema also represents `owned_or_licensed` and `open_license` rights bases for future content with documented permission.
Do not use either rights basis without retaining the applicable permission or license evidence and required attribution.

An official-link entry must not contain a copied statement, example, test, editorial, or reference implementation.
It must also leave constraints, complexity guidance, solution references, misconceptions, hints, edge families, and follow-ups empty.
Its learner workspace contains only the official URL, stable problem reference, and an empty local scaffold.
External content remains on the provider's page.

Do not add scraped content, content with unclear provenance, or unreviewed model-generated durable entries.
If redistribution rights are uncertain, stop the contribution and request a rights decision.

Private learner entries belong under the user's openlearn home, not in the Python package.
When a repository-local development copy is necessary, use `interview-problems/private/`, which Git ignores.
The private loader reads that directory separately and never merges its contents into the bundled catalog.
It opens each entry once without following its final symlink where the platform supports that flag, validates the opened descriptor, and performs a bounded read.

## Version And Checksum Contract

The catalog has a stable `catalog_id` and monotonically increasing `catalog_revision`.
Each problem has a stable ID, an integer revision, and a SHA-256 checksum of its canonical JSON content.
The catalog retains multiple immutable revisions under the `(problem_id, revision)` key.
Each record declares the catalog revision that first introduced it.
The catalog also has a SHA-256 checksum covering the complete catalog.

Any durable attempt reference must record:

- catalog ID and revision;
- problem ID and revision;
- problem checksum.

Changing learner-visible content, interface, tests, skills, or teaching metadata requires a new problem revision and catalog revision.
Existing revisions must remain available while durable attempts still reference them.
Resolution checks the recorded problem checksum and accepts an older catalog revision only while that exact problem revision remains present.
Do not silently replace the meaning of a stable revision.

## Schema Coverage

A packaged problem declares:

- statement, source, rights, attribution, and permissions;
- supported language interfaces, starter code, and trusted reference symbol;
- difficulty and expected completion time;
- primary and supporting skill IDs from the exact interview graph version;
- the exact graph and mastery-policy versions, canonical transfer family, and an explicit mastery-evidence eligibility flag;
- prerequisite problem links and deliberate near-duplicate exclusions;
- constraints, original examples, and edge-case families;
- public and hidden deterministic JSON test cases;
- solution references, expected time and space complexity, misconceptions, and progressive hints;
- follow-up prompts and valid target problem links;
- immutable problem and catalog checksums.

Official-link entries retain selection metadata and a local interface scaffold but package no protected execution or feedback content.
They are explicitly ineligible for mastery evidence.
Starter scaffolds contain exactly one inert function with a single `pass` body and cannot execute code on import.

## Contributor Workflow

1. Write the problem in original language or document unambiguous redistribution rights.
2. Add explicit license, attribution, permission, and HTTPS source metadata.
3. Use existing stable skill IDs from the catalog's pinned graph version.
4. Add deterministic public and hidden cases using finite JSON values.
5. Add a trusted reference implementation under `openlearn.interview_problem_references`.
6. Run the reference against every packaged case and inspect all optional similarity flags.
7. Recompute the problem checksum, then recompute the catalog checksum.
8. Run the focused catalog tests and the complete repository gate.
9. Have another reviewer inspect both the rights evidence and the problem quality.

The deterministic validation command is:

```bash
python -m openlearn.interview_catalog validate
```

The focused test command is:

```bash
pytest -q tests/test_interview_catalog.py
```

Validation rejects unknown or duplicate identities, stale checksums, malformed interfaces, missing rights metadata, invalid problem links, non-JSON tests, wrong expected outputs, and reference implementations that fail packaged cases.
Similarity flags are advisory review prompts, not automatic plagiarism or rights conclusions.
