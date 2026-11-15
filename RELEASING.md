# Releasing the engine

For the maintainer. Readers' instances take the latest release tag, never `main`: a release is what every paper runs next Saturday.

- **Changes reach `main` only through pull requests**, including the ones runs file (`FEEDBACK.md`).
- **Compatibility:** a new `reader.md` setting always has a default in `tools/paths.py`. Anything an instance has to change (a file, a setting, a template override) goes under "Instance changes" in `CHANGELOG.md` with exact steps; the dreaming run makes them before its self-test. A release that breaks an instance which skips those steps is a major version.
- **Changelog:** add the version's section to `CHANGELOG.md` (newest first): "What changes in your paper", in the reader's terms, and "Instance changes" when there are any. Candidates share the final version's section.
- **Candidate:** tag `vX.Y.Z-rc1` on `main` and push the tag. Let an instance set to `engine_updates: rc` run one dreaming and one week-ahead cycle on it (the dreaming run installs it). Fix and cut `-rc2` if needed.
- **Final:** tag `vX.Y.Z` on the candidate's commit (or a later one) and push the tag, then publish a GitHub release with the version's `CHANGELOG.md` section: `gh release create vX.Y.Z --notes "<that section>"`.
- Versions: `vMAJOR.MINOR.PATCH`; no version file, the tag is the version. Never move or delete a published tag.
