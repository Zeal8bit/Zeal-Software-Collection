# Community TF image

This guide covers local builds, troubleshooting, and publishing. For downloads
and installation instructions, see the [README](README.md#community-tf-image)
and the [latest release](https://github.com/Zeal8bit/Zeal-Software-Collection/releases/latest).

## Build locally

You need Python 3.10 or newer, Git, Docker or Podman with a Compose provider,
a running Linux container engine, and internet access. On macOS, use Docker
Desktop or an equivalent Linux engine. Image creation requires privileged
containers and FUSE. No GitHub account, token, or GitHub CLI is needed to build.

Run from the Software Collection checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install 'PyYAML==6.0.3'
.venv/bin/python assets/scripts/build_community_image.py
```

The build uses this checkout's `collection.yml`. Use `CONTAINER_CMD=podman` to
select Podman explicitly:

```sh
CONTAINER_CMD=podman .venv/bin/python assets/scripts/build_community_image.py
```

To build without reading or updating the build cache:

```sh
.venv/bin/python assets/scripts/build_community_image.py --no-cache
```

To choose another output directory under this checkout's `dist/`:

```sh
.venv/bin/python assets/scripts/build_community_image.py --output dist/my-community-image
```

## Find output and diagnose failures

Completed output is in `dist/community/`, or the directory selected with `--output`:

| File | Use |
| --- | --- |
| `tf-{size}mib.img.zip` | Download bundle; extract before flashing |
| `tf-{size}mib.img` | Raw image in 2, 16, 64, or 128 MiB capacity |
| `tf-{size}mib.img.sha256` | Image checksum |
| `README.md` | Included software, TF file tree, and failed or skipped projects |
| `build-report.json` | Build results and source revisions |
| `build.log` | Detailed command output for troubleshooting |
| `content-manifest.json` | File inventory and hashes |

Each image ZIP includes license notices and links to matching source code.
`distribution.zip` and its checksum are packaging intermediates; use the
`tf-{size}mib.img.zip` files for distribution.

Check an extracted image's checksum with:

```sh
cd dist/community
shasum -a 256 -c tf-2mib.img.sha256
```

For live progress, use the workspace path printed at startup. Its `artifacts/`
directory contains the current log and report. Workspaces are retained under
`tmp/community-image/` for inspection.

If a build fails, inspect `build.log` and `build-report.json` in the output
directory. Some projects may be absent even when an image is produced; check the
included, failed, and skipped lists before distributing it. A failed run can
replace the latest output with diagnostics, so use an earlier retained workspace
if you need its image.

Remove completed workspace directories under `tmp/community-image/` when no
longer needed. To clear the build cache, remove `tmp/community-image-cache/`
when no builds are running.

## Retry one app

To update and rebuild an app in the last build's retained workspace:

```sh
.venv/bin/python assets/scripts/build_community_image.py --retry TurBoss/Zeal-8bit_conway
```

ZDE aliases also work. Add `--output dist/my-community-image` to select another
completed bundle. Inspect retry logs under `<workspace>/retries/`.

A retry does not update the TF image. Run a full build to include the result or
apply changes to `collection.yml`.

## GitHub Actions and publishing

The **Community TF image** workflow runs when `collection.yml` changes on `main`
or `feature/community-image`, nightly at **07:17 UTC**, or manually through
**Actions → Community TF image → Run workflow**. Branch builds create draft
releases. Main builds publish when release contents change.

For failed runs, inspect the Actions log and download the `community-image`
artifact. Artifacts are retained for 14 days.

To publish a completed local bundle, install GitHub CLI and authenticate with
release-write access to the destination repository:

```sh
.venv/bin/python assets/scripts/publish_community_image.py --repo OWNER/REPO
```

Use `--directory dist/my-community-image` to select another bundle. Add `--draft`
to create an unpublished preview. Review the release's project changelog and
included/failed lists. If an upload fails, remove the incomplete draft before
retrying.

## Run tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```
