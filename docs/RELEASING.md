# Publishing a release

Starting with v1.3.0b2, HACS installs the dedicated `sigen.zip` asset. Its contents are the committed files under `custom_components/sigen/`, without an enclosing directory.

1. Set the manifest version, for example `1.3.0b2`. Keep `hacs.json` configured with `zip_release: true` and `filename: sigen.zip`.
2. Add optional release notes in `docs/releases/<version>.md`. Otherwise GitHub generates the notes.
3. Commit and push the release preparation to the intended branch. Wait for HACS, Hassfest, and regression checks to pass.
4. Create and push the matching tag, for example `v.1.3.0b2`, on that exact commit. Do not create a published release manually first.
5. The **Publish installation ZIP** workflow tests both supported Modbus versions, runs HACS/Hassfest, builds and checks the package, uploads it to a draft release, downloads and compares the uploaded bytes, then publishes with an Announcements discussion.
6. Check the published asset, tag, manifest version, and prerelease status. Test installation through HACS and report hardware results in the beta discussion.

Alpha, beta, and release-candidate manifest suffixes (`a1`, `b1`, `rc1`) produce prereleases and leave the latest stable release unchanged. Stable versions are marked latest when published. The workflow supports three- and four-part numeric versions used by this repository.

The workflow can be retried after a failure with a draft release. It may replace an asset in that unpublished draft, but refuses to replace a published release. Never move published tags or replace assets whose download counters are already in use; prepare a new version instead.

For a local package check after committing:

```sh
python scripts/build_release.py --tag v.1.3.0b2 --output dist/sigen.zip
```

The builder always reads `HEAD` through Git, so local caches and uncommitted edits are excluded. A tag/version mismatch stops the build. The package is checked against the committed source before upload.

Download counters measure downloads of each release asset, including reinstallations and validation downloads. Old source-archive downloads are unavailable. HACS views that still select an older source-archive release may continue to show no download count until a ZIP-based release is selected.
