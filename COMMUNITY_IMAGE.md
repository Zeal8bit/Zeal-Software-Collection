# Community TF image

Build the community image from the Software Collection checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install 'PyYAML==6.0.3'
python3 assets/scripts/build_community_image.py
```

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

Within a run directory, `zde/` is `ZDE_PATH`, `state/` is `ZDE_USER_PATH`,
`zde/mnt/tf/` contains staged files, and `zde/mnt/tf.img` contains the image after
creation. Your shell environment and system ZDE dependency installations are not
changed. The Docker/Podman container image cache is shared.

## Output

The default output directory is `dist/community/`:

- `tf.img`: verified 64 MiB ZealFS v2 TF image with an MBR.
- `tf.img.sha256`: image checksum; run `sha256sum -c tf.img.sha256` from this directory.
- `content-manifest.json`: sorted image-visible paths, directory entries, file sizes,
  and SHA-256 hashes, with a combined content fingerprint.
- `build-report.json`: included, failed, and skipped items, source revisions,
  excluded filenames, ZDE revisions, and overall build status.
- `build.log`: ZDE command output for diagnosing individual failures.

Use `--output dist/my-community-image` to select another output directory under
this checkout's `dist/`. Generated output is ignored by Git. Rerunning the builder
removes previous image output first, so a failed build cannot leave an old image
appearing to be a new successful build. Save any image you want to retain elsewhere.

## Installation and failure handling

The builder loads this checkout's `collection.yml` as ZDE's collection catalog.
ZDE resolves the effective catalog, including built-in settings, configured refs,
dependencies, build instructions, artifact paths, and staging destinations. As in
ZDE itself, built-in catalog settings take precedence for matching IDs. Explicit
collection tags and commits remain pinned; nightly builds do not override them.

Each ZDE-visible item gets its own `zde deps install <id>` invocation, in dependency
order. The OS and VideoBoard SDK are bootstrapped first, followed by collection
libraries before applications, because some projects use SDK packages without
declaring dependency edges. Additional dependencies and ZealFS image tools are installed as needed.
Individual failures do not stop attempts for the remaining items. A dependent
whose prerequisite failed is not included, even if its repository exists on disk.
ZDE installs/builds fresh dependencies; no previous build cache is reused.

Successful installs are staged individually using `zde deps stage tf <id>`.
Intentionally disabled staging is recorded as skipped. Failed staging is rolled
back, including partial copies. An item that overwrites a previously staged path
with different contents is rejected and rolled back; identical shared files are
allowed. Items contributing no additional image-visible paths are recorded as
skipped. `zde: false` collection entries are not selected.

ZDE excludes names longer than 16 UTF-8 bytes and their subtrees from ZealFS images.
The manifest uses the same rule and follows symlinks as ZDE does. Exclusions are
recorded in the build report. Unsupported file types, broken symlinks, and symlink
cycles fail verification rather than silently omitting data.

Once staging completes, the builder runs:

```sh
zde image tf create --yes 65536
```

It then mounts the generated image inside ZDE and checks the full file and directory
manifest against staging. Only a matching image is copied to the output directory.
Individual item failures allow a partial image. Image-tool failures, image-creation
or verification failures, or no successfully staged software make the build fail.
A build report is written on failure; logs are retained once ZDE commands begin.

## GitHub automation

`.github/workflows/community-image.yml` runs the same builder on every push to
`main`, nightly at **07:17 UTC**, and through **Run workflow** in GitHub Actions.
Workflow runs are serialized. Reports and logs are retained as workflow artifacts
for 14 days, including on failed builds when output exists.

A separate publishing job downloads successful build output. Its release credential
is available only to the publishing step; collection build commands run with
read-only repository permissions and no persisted checkout credential.

The publisher compares against the newest published release whose tag starts with
`community-image-`, ignoring drafts and unrelated releases. It validates both
manifests and compares content fingerprints. Files added, removed, or changed,
and directory changes, cause a new release. Timestamps, reports, build dates, and
source revisions do not. A changed failure report alone does not create a release.
A partial image that loses files compared with the previous release does count as
a changed image.

The first successful image is published. Later unchanged builds finish without a
release. API errors, missing baseline assets, or malformed manifests stop publishing
rather than being treated as a first build.

Changed builds become normal, dated releases with unique UTC timestamp/run tags,
marked as latest. Each release includes `tf.img`, its checksum, the content manifest,
and build report. Its description lists included software, explicitly names failed
items that were not included with their failure stage, and lists skipped items and
excluded filenames. All assets are uploaded to a draft before publication. If an
upload fails, the draft remains unpublished and is ignored by future comparisons;
maintainers can remove the incomplete draft.

Publishing can also be run separately with GitHub CLI and release-write access:

```sh
GH_TOKEN=... python3 assets/scripts/publish_community_image.py --repo OWNER/REPO
```

This is separate from local image generation and publishes to the specified repo.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests exercise content identity, individual failures and dependency ordering,
staging rollback, collisions, release selection, unchanged builds, invalid baseline
handling, and failure before release publication. A full build additionally requires
the container prerequisites above and verifies the mounted image before succeeding.
