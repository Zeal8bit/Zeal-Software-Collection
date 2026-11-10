# Community TF image

Build the community image from the Software Collection checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install 'PyYAML==6.0.3'
python3 assets/scripts/build_community_image.py
```

For later runs, use `.venv/bin/python assets/scripts/build_community_image.py`, or
activate `.venv` before running the script directly. If PyYAML is missing from the
selected Python, the builder stops immediately with setup instructions before
creating a workspace or contacting the container engine.

You need Python 3.10 or newer, Git, Docker or Podman with a Compose provider, and a running
Linux container engine. On macOS, use Docker Desktop or an equivalent Linux Docker
engine. Internet access is needed to download ZDE, its container, and collection
repositories. Image creation uses ZDE's standard privileged container and FUSE.
The builder selects Docker when available, otherwise Podman; `CONTAINER_CMD=podman`
explicitly selects Podman. No GitHub account, token, or GitHub CLI is required to
build locally.

Each invocation creates a fresh ZDE checkout from upstream `main`, including its
submodules, and pulls the published `zoul0813/zeal-dev-environment:latest` container.
It uses the pulled image's immutable digest for the rest of the build. The
checkout and ZDE user state are isolated from your normal ZDE installation. ZDE
source is not modified.

Each invocation uses one workspace under `tmp/community-image/<unique-run>/` in
this repo. All dependency installs, builds, and staging operations for that
invocation share it. Workspaces are ignored by Git and retained after success or
failure so you can inspect them; remove completed run directories when no longer
needed. The absolute workspace path is recorded in `build-report.json`.

Each run owns its live output bundle and diagnostics in `artifacts/`, including
failure reports. When the run finishes, it updates `dist/community/` with that
completed bundle. Concurrent builds keep separate live reports; exporting and
publishing the latest bundle use a shared lock to keep image/report pairs consistent.

Within a run directory, `zde/` is `ZDE_PATH`, `state/` is `ZDE_USER_PATH`,
`zde/mnt/tf/` contains staged files, and `zde/mnt/tf.img` contains the image after
creation. Your shell environment and system ZDE dependency installations are not
changed. The Docker/Podman container image cache is shared.

## Reusing unchanged builds

Builds automatically reuse successful project installations from
`tmp/community-image-cache/`. Each run still has its own workspace, checks the
configured upstream refs, stages files, collects licenses, and creates and verifies
all four image capacities.

A project is rebuilt from a clean checkout when its commit, effective build
recipe, or any direct or transitive dependency's build inputs change. Every
project implicitly depends on the ZOS kernel: a kernel commit change rebuilds
everything. Changing ZDE, the pinned container digest, or the builder/cache code
also invalidates cached builds. An unchanged project can reuse its binaries,
libraries, and other generated files, with cache hits and misses recorded per
item in `build-report.json`.

Only successful installs and staging results are saved. Failed projects and their
dependents are retried next time. Missing or corrupt caches fall back to fresh
builds. Concurrent runs use separate restored installations and share a lock only
while copying or publishing the cache snapshot. Interrupted cache writes are
ignored; the cache is optional, and generated images are always verified.

For a completely fresh build that neither reads nor updates the build cache:

```sh
python3 assets/scripts/build_community_image.py --no-cache
```

Remove `tmp/community-image-cache/` when no builds are using it to clear the local
cache. GitHub nightlies restore and save the same snapshot using GitHub Actions
Cache. PR collection validation continues to use fresh environments.

## Retry one app

After a build, update and rebuild a single app in its retained workspace:

```sh
python3 assets/scripts/build_community_image.py --retry TurBoss/Zeal-8bit_conway
```

ZDE aliases also work. The command uses the workspace referenced by
`dist/community/build-report.json`, including its isolated dependencies, collection
snapshot, configured refs, and pinned container image. Use `--output dist/OTHER`
to select another completed bundle. It runs `zde deps install` followed by
`zde deps build`, stops on failure, and returns a nonzero exit code.

Each attempt saves a separate `build.log` and `report.json` under
`<workspace>/retries/<unique-attempt>/`. It updates source/build files in that
workspace but does not stage files or change the image, manifest, README, or
original build report. Run a full build when you want a new image. Changes to
this checkout's `collection.yml` require a full build to take effect.

## Output

Live output is under `<workspace>/artifacts/`. The completed bundle is also
exported to `dist/community/` by default:

- `tf-{size}mib.img`: verified ZealFS v2 TF images with an MBR, in **2, 16, 64, and 128 MiB** capacities.
- `tf-{size}mib.img.sha256`: per-image checksum; for example, run `sha256sum -c tf-2mib.img.sha256`.
- `tf-{size}mib.img.zip`: compressed download containing that image, its checksum,
  and the license notices and pinned source-download links described below.
  Extract before flashing. Every capacity contains the same software; larger images
  provide more free filesystem space after flashing. Empty space compresses well,
  so download sizes are much smaller than the extracted images.
- `README.md`: the same build description used for GitHub releases, including each project’s newly added TF files, the verified TF directory tree, and failed and skipped software.
- `content-manifest.json`: sorted image-visible paths, directory entries, file sizes,
  and SHA-256 hashes, with a combined content fingerprint.
- `build-report.json`: live install/staging progress, included, failed, and skipped
  items, source revisions, excluded filenames, the collection checksum, ZDE
  revisions, and overall build status.
- `build.log`: ZDE command output for diagnosing individual failures.
- `distribution.zip` and `distribution.zip.sha256`: internal intermediates used to
  put identical licenses and notices into each image ZIP. They are not uploaded
  as separate release downloads.

Use `--output dist/my-community-image` to select another output directory under
this checkout's `dist/`. Generated output is ignored by Git. A new run writes to
its own workspace first. If the run fails, its completed
failure bundle replaces the latest output and removes the previous latest image;
previous run workspaces retain their own files. Monitor the printed workspace
path for live progress, and use `dist/community/` for the latest completed run.

## Licenses in the ZIP downloads

Each image ZIP includes the projects' license and notice files under
`licenses/<owner>/<project>/`, copied from their Git clones, with an index in
`THIRD_PARTY_NOTICES.md`. Each project has a direct GitHub source-download link
pinned to the exact build commit, including required runtimes and dependencies.
Source code is not bundled in the ZIPs. License files and notices are outside
`tf.img` and do not consume space on the flashed filesystem.

## Installation and failure handling

The builder loads this checkout's `collection.yml` as ZDE's collection catalog.
ZDE resolves the effective catalog, including built-in settings, configured refs,
dependencies, build instructions, artifact paths, and staging destinations. As in
ZDE itself, built-in catalog settings take precedence for matching IDs. Explicit
collection tags and commits remain pinned; nightly builds do not override them.

Each ZDE-visible item is installed individually through ZDE, in dependency order.
Required dependencies, including the OS, are installed first. Collection entries declare additional SDK and library
prerequisites, so ZDE can install each project in a fresh workspace. Additional dependencies and ZealFS image tools are installed as needed.
Individual failures do not stop attempts for the remaining items. A dependent
whose prerequisite failed is not included, even if its repository exists on disk.
The builder installs dependencies in order using ZDE's per-dependency API, so
syncing a child does not reset a cached parent's generated SDK libraries.
Changed build inputs force a clean installation rather than an incremental Make
or CMake build that might retain stale outputs.

Successful installs are staged individually using `zde deps stage tf <id>`. The
isolated ZDE configuration enables `deps.rename-bins`, removing the `.bin`
extension from individually staged binary artifacts. The PR validation check
uses the same setting.
Intentionally disabled staging is recorded as skipped. Failed staging is rolled
back, including partial copies. An item that overwrites a previously staged path
with different contents is rejected and rolled back; identical shared files are
allowed. Items contributing no additional image-visible paths are recorded as
skipped. `zde: false` collection entries are not selected.

ZDE excludes names longer than 16 UTF-8 bytes and their subtrees from ZealFS images.
The manifest uses the same rule and follows symlinks as ZDE does. Exclusions are
recorded in the build report. Unsupported file types, broken symlinks, and symlink
cycles fail verification rather than silently omitting data.

Once staging completes, the builder creates each capacity using ZDE (sizes are in KiB):

```sh
zde image tf create --yes 2048
zde image tf create --yes 16384
zde image tf create --yes 65536
zde image tf create --yes 131072
```

It mounts each generated image inside ZDE and checks the full file and directory
manifest against the same staging tree, and checks the image byte size. Only matching
images are copied to the output directory. All four sizes must succeed, including
fitting all staged contents into the 2 MiB image; overflow fails the build.
Individual item failures allow a partial image. Image-tool failures, image-creation
or verification failures, or no successfully staged software make the build fail.
A build report is written on failure; logs are retained once ZDE commands begin.

## GitHub automation

Pull requests changing the collection run a separate check that compares entries
with the PR base revision. New and modified ZDE-buildable entries are installed
and staged in separate fresh ZDE environments, with up to four checked in
parallel. Each environment includes ZDE-required dependencies and the entry's
declared dependency chain. Entries marked `zde: false` or `build: false` are
skipped; removals require no build. The check does not create a TF image. It
can catch missing dependencies when the build exercises them, but dependencies
in ZDE's required baseline are always present. Its output is in the Actions log.

`.github/workflows/community-image.yml` runs the same builder on pushes that change
`collection.yml` on `main` and `feature/community-image`, nightly at **07:17 UTC**, and through
**Run workflow** in GitHub Actions once the workflow exists on the default branch.
Workflow runs are serialized. Reports and logs are retained as workflow artifacts
for 14 days, including on failed builds when output exists.

Successful branch builds create a draft release with all download assets, even
when image contents are unchanged. Drafts remain unpublished and are not marked
latest. Builds on `main` publish only changed images as described below.

A separate publishing job downloads successful build output. Its release credential
is available only to the publishing step; collection build commands run with
read-only repository permissions and no persisted checkout credential.

The publisher compares against the newest published release whose tag starts with
`community-image-`, ignoring drafts and unrelated releases. It validates both
manifests and compares content fingerprints. Files added, removed, or changed,
and directory changes, cause a new release. License notices or pinned source-download
links changing also cause a release. Timestamps, reports, and build dates alone do
not. Missing required download assets also cause a new release,
so older releases are replaced with the complete capacity selection. A changed
failure report alone does not create a release.
A partial image that loses files compared with the previous release does count as
a changed image.

The first successful image is published. Later unchanged builds finish without a
release. API errors, missing baseline assets, or malformed manifests stop publishing
rather than being treated as a first build.

Changed builds become normal, dated releases with unique UTC timestamp/run tags,
marked as latest. Each release includes four ZIP downloads and their image checksums,
the content manifest, and build report. Raw images remain in the local build bundle;
GitHub releases distribute the compressed images. Its description lists included software, explicitly names failed
items that were not included with their failure stage, and lists skipped items and
excluded filenames. All assets are uploaded to a draft before publication. If an
upload fails, the draft remains unpublished and is ignored by future comparisons;
maintainers can remove the incomplete draft.

Publishing can also be run separately with GitHub CLI and release-write access:

```sh
GH_TOKEN=... python3 assets/scripts/publish_community_image.py --repo OWNER/REPO
```

Add `--draft` to create an unpublished preview with all assets instead.

This is separate from local image generation and publishes to the specified repo.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests exercise content identity, individual failures and dependency ordering,
staging rollback, collisions, release selection, unchanged builds, invalid baseline
handling, and failure before release publication. A full build additionally requires
the container prerequisites above and verifies the mounted image before succeeding.
