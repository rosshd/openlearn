# Releasing openLearn

This runbook publishes one verified wheel and source distribution for a matching `vX.Y.Z` tag.
Do not publish from a dirty tree or reuse, move, or overwrite a release tag.
Learner homes are outside package artifacts and must not be changed by release work.

## Prepare a release candidate

1. Update `src/openlearn/__init__.py` to the intended version and merge that release commit to `main`.
2. Install the development environment with `python -m pip install -e ".[dev]"`.
3. Run `make release-build` once from that exact commit.
4. Keep the resulting `.artifacts/release-candidate/` directory immutable.
5. Run `make release-verify`, `make release-smoke-wheel`, and `make release-smoke-sdist` against that same candidate.
6. Complete the installed-artifact CLI and Maker Bench journey in [the manual-test guide](../manual-tests/README.txt) without importing the source checkout.
7. Optionally run `make review` to collect local gate evidence, then confirm the `Tests` workflow is green for the exact release commit on `main`.
8. Compare the local `SHA256SUMS` file with the candidate attached to that successful `Tests` run.

The tag version, `openlearn.__version__`, and `openlearn --version` must agree.
The `Tests` workflow builds the wheel and source distribution once, records their hashes, and fans the exact candidate out to the supported installed-package matrix.
The tag workflow downloads the successful `Tests` candidate for the exact tagged commit, verifies its hashes and version again, and never rebuilds it.
It publishes those exact distribution files through PyPI trusted publishing before creating the GitHub Release from the same files and `SHA256SUMS`.

## Publish

Complete the five-person release-candidate gate in [`manual-tests/public-release.md`](../manual-tests/public-release.md) against the exact candidate from the successful `Tests` run.
Do not create or push the release tag while any tester journey, required platform, recovery probe, or release blocker remains pending.
Select the public version only after the sanitized gate record is complete and its candidate hashes still match.

After all checks pass, push the new immutable tag:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

Record the candidate hashes and compare them with both public destinations before announcing the release.
If the workflow cannot prove the matching artifact or a required gate fails, stop publication and fix the release candidate on a new commit and new version.

## Simulated partial-publication drill

Practice this procedure in a non-production test project or a dry-run exercise before a public release.

1. Record the intended version, commit, tag, artifact filenames, and hashes.
2. Simulate one public destination succeeding while the other is withheld.
3. Verify whether an end user can install the published artifact and whether its version and hash match the release record.
4. If no public artifact was published, repair the candidate, rerun all gates, and publish the original version only if the tag was never made public.
5. If any public artifact was published, do not replace it or move its tag.
6. Publish a corrected higher version, document the affected version and remedy, and withdraw or mark the bad GitHub Release as appropriate.
7. Preserve the original evidence, including hashes and failure timeline, without collecting learner data.

## Corrected or withdrawn releases

Treat an incorrect public package as immutable.
Do not delete, rewrite, or silently replace a public version in a way that changes what existing installations receive.
Release a higher corrected version, publish concise release notes describing the impact and upgrade path, and use the package registry's available yanking or withdrawal mechanism when it is appropriate.
If a GitHub Release is wrong but the package is not published, correct the release metadata or remove the erroneous draft without changing any learner home.
For a credential exposure or compromised artifact, follow [the security policy](../SECURITY.md), revoke affected credentials, and publish a corrected higher version.
