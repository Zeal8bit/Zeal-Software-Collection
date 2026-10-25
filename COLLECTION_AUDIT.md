# Collection build audit — 2026-10-25

This audit used fresh upstream checkouts at the revisions recorded in the local
build report, the projects' CMake/Make entrypoints and upstream build documentation,
and a complete isolated Podman build. Third-party project source was not modified.
Existing branch/tag pins and ZDE/build opt-outs are preserved.

## Dependency declarations

22 entries were missing direct SDK or library prerequisites. These are now
explicit in `collection.yml`, allowing ZDE to order each project's installation
without relying on dependencies left over in a developer's environment. The
community builder uses the dependency graph rather than a category-based ordering
workaround.

ZDE installs the required OS during environment setup; collection entries do not
declare it as a dependency. Video SDK means
`Zeal8bit/Zeal-VideoBoard-SDK`; ZGDK means `zoul0813/zeal-game-dev-kit`.
Coreutils and Archiver refer to `zoul0813/zeal-coreutils` and
`zoul0813/zeal-archiver`.

| Entry | Added direct prerequisites |
| --- | --- |
| zoul0813/zeal-zfighter | Video SDK, Coreutils, ZGDK |
| zoul0813/zeal-microbe | Video SDK, Archiver, ZGDK |
| zoul0813/zeal-bricked | Video SDK, Coreutils, ZGDK |
| zoul0813/zeal-closure | Video SDK, ZGDK |
| Zeal8bit/Zeal-Chip8 | Video SDK |
| harlock74/zeal-pongy | Video SDK, Coreutils, ZGDK |
| harlock74/zeal-videopoker | Video SDK, Coreutils, ZGDK |
| harlock74/zeal-couples | Video SDK, Coreutils, ZGDK |
| harlock74/zeal-solitaire | Video SDK, Coreutils, ZGDK |
| CaPusto/zealmine | Video SDK, Coreutils, ZGDK |
| harlock74/zeal-checkers | Video SDK, Coreutils, ZGDK |
| harlock74/zeal-minigolf | Video SDK, Coreutils, ZGDK |
| zoul0813/zeal-boing | ZGDK |
| zoul0813/zeal-matrix | ZGDK |
| zoul0813/zeal-truchet | Video SDK |
| zoul0813/zeal-zshell | Video SDK |
| zoul0813/zeal-piano | Video SDK, ZGDK |
| zoul0813/zeal-music-tracker | Video SDK, ZGDK |
| zoul0813/zeal-commander | Video SDK, Coreutils |
| zoul0813/zeal-cc | Video SDK, Coreutils |
| zoul0813/zeal-game-dev-kit | Coreutils |
| zoul0813/zeal-archiver | Coreutils |

ZDE's native resolver accepted the full 48-entry catalog. In a fresh project-local
workspace, Microbe resolved to OS, Video SDK, Coreutils, Archiver, ZGDK, and then
Microbe itself, with no prerequisites already installed. The collection tests also check unique IDs, complete dependency
references, acyclic ordering, and Microbe's prerequisite chain.

## Build and staging corrections

| Entry | Correction | Verification |
| --- | --- | --- |
| Snake | Build the SDK's `examples/snake` CMake subproject and stage `build-snake/snake.bin` | Fresh install reaches the actual example build; upstream asset-symbol linking currently fails |
| BBC Basic | Stage `build/bbcbasic.bin`, matching its current CMake output | Staged into the verified TF image |
| Onomata | Stage the upstream-documented, committed `C-heapless/zos/onoi` Zeal binary | Staged into the verified TF image; the legacy source Makefile is not selected implicitly |
| Zeal Forth | Stage the public `bin/` output, including its companion `forth.fs` library | Interpreter and library staged into the verified TF image |

Onomata's upstream `C-heapless/README.md` documents the bundled Zeal binary. Its
source build still references the old OS layout; this entry installs that published
binary rather than a host-target implementation.

## Remaining upstream compatibility failures

Dependencies are present for these entries. Their current build failures need
compatibility work in the upstream projects or a separately reviewed supported
version combination; dependency declarations alone do not resolve them.

| Entries | Observed failure |
| --- | --- |
| 2048, Game of Life | Makefiles require `kernel_headers/sdcc/bin/zos_crt0.rel`; current OS uses `sdk/` |
| CHIP-8, Zepto, Zealasm, Zeal Mint | Legacy OS include paths or syscall/keyboard symbols fail assembly |
| Zeal Boing, Zeal Matrix, Zeal QR, Zeal Monitor | Make includes reference the removed `kernel_headers/sdcc/base_sdcc.mk` layout |
| Zeal Plasma | Legacy assembly syscall macros and symbols fail with the current OS SDK |
| Zeal Truchet | DMA functions referenced by the application are unresolved at link time |
| Snake | Current example and OS-generated asset symbols disagree (`__numbers_start`, `__letters_start`, `__snake_tileset_start`, and related symbols) |

No forks, source patches, generated compatibility headers, or altered toolchain
assets were introduced to work around these failures. Each failed item remains
visible in the build report and release description.

## Image verification

The full clean baseline built an image with 22 included, 15 failed, and 11 skipped
entries. Targeted verification then applied the corrected BBC Basic, Onomata, and
Forth staging through ZDE, regenerated the image, and checked its complete mounted
file/directory manifest against staging. The corrected validation image has
**24 included, 13 failed, and 11 skipped** entries, including Microbe.

The local report records the baseline plus targeted validation, the collection
checksum, and retained workspace. The complete image build was not repeated after
these catalog edits; the changed staging recipes were exercised separately, and
Snake was checked in its own fresh workspace. GitHub release publication was tested
with mocked calls; no release or remote branch was published.
