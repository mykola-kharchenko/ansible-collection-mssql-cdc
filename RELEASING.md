# Releasing

End-to-end checklist for cutting a new release of
`mykola_kharchenko.mssql_cdc`.

## Pre-flight

- [ ] All CI checks green on `main`.
- [ ] `ansible-test sanity --python 3.12` clean locally.
- [ ] `pytest tests/integration -v` clean against a real SQL Server (see
      [tests/integration](tests/integration/)).
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` with the user-visible
      changes since the last release.
- [ ] `galaxy.yml` `version` bumped (SemVer).
- [ ] Any new modules / options documented in their `DOCUMENTATION` blocks and
      reachable from `README.md`.

## Cut the release

```bash
# Move [Unreleased] entries under the new heading + add the version footer link.
$EDITOR CHANGELOG.md

# Bump galaxy.yml version (must match the tag below, without the leading v).
$EDITOR galaxy.yml

git commit -am "chore: release 0.X.Y"
git tag -a v0.X.Y -m "0.X.Y"
git push origin main v0.X.Y
```

The `Publish to Ansible Galaxy` workflow fires on the `v*` tag, builds the
tarball with `ansible-galaxy collection build`, and uploads it via
`ansible-galaxy collection publish` using the `ANSIBLE_GALAXY_API_KEY` secret
stored on the **Ansible Galaxy** environment.

## One-time setup

1. **Galaxy API key.** Sign in at <https://galaxy.ansible.com> with the owning
   account, generate a key under *Preferences → API key*.
2. **GitHub environment.** Repo *Settings → Environments → New environment →
   `Ansible Galaxy`* and add `ANSIBLE_GALAXY_API_KEY` as a secret.
3. (Optional) Add required reviewers on the *Ansible Galaxy* environment so a
   release needs an approval click before publishing.

## Rolling back

Galaxy does not delete published versions, but you can yank them from the UI
(*Versions → Hide*) so they are no longer downloaded by default. Cut a new
patch with the fix and let users `--force` upgrade.
