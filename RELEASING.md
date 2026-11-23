# Releasing the engine

For the maintainer. Readers' instances take the latest release tag, never `main`: a release is what every paper runs next Saturday.

- **Contributions arrive as pull requests**, including the ones readers' runs file (`FEEDBACK.md`); the maintainer reviews and merges them. The maintainer may also commit to `main` directly: `main` reaches no paper until it is tagged.
- **Compatibility:** a new `reader.md` setting always has a default in `tools/paths.py`. Anything an instance has to change (a file, a setting, a template override) goes under "Instance changes" in `CHANGELOG.md` with exact steps; the dreaming run makes them before its self-test. A release that breaks an instance which skips those steps is a major version.
- **Changelog:** add the version's section to `CHANGELOG.md` (newest first): "What changes in your paper", in the reader's terms, and "Instance changes" when there are any. Candidates share the final version's section.
- **Candidate or not:** a release that changes the method (`RUN.md`, `FEEDBACK.md`, templates) or how updates install goes out as a candidate first, because only a real run shows whether the model follows the new text. A release that only fixes tools goes out directly as the next patch version (`v0.2.0` → `v0.2.1`): the dreaming run's self-test checks that the tools work, and the run rolls back if they don't.
- **Candidate:** tag `vX.Y.Z-rc1` on `main` and push the tag. Let an instance set to `engine_updates: rc` run one dreaming and one week-ahead cycle on it (the dreaming run installs it). Fix and cut `-rc2` if needed.
- **Feedback relay:** before tagging a final release whose SETUP offers `engine_feedback: relay`, the relay (`relay/README.md`) must be deployed and its URL set as the `feedback_relay` default in `tools/paths.py`; until then the client treats the placeholder as not configured.
- **Final:** tag `vX.Y.Z` on the candidate's commit (or a later one) and push the tag, then publish a GitHub release with the version's `CHANGELOG.md` section: `gh release create vX.Y.Z --notes "<that section>"`.
- Versions: `vMAJOR.MINOR.PATCH`; no version file, the tag is the version. Never move or delete a published tag.
