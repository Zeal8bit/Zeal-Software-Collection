# Zeal 8-bit Computer software collection

In this repository, you will find a list of existing software for Zeal 8-bit Computer.

Try pre-built software with the [community TF image](#community-tf-image).

Projects marked with:

* 🪛 involve a hardware extension.
* ⚠️ involve a *Work In Progress* state, may not be usable yet.
{{- $categories := partial "util/categories.html" . -}}
{{- range $category := $categories }}
  {{- $emoji := partial "util/category-emoji.html" $category }}
  {{- $label := partial "util/category-label.html" $category }}
  {{- $entries := partial "util/dependencies-for-category.html" (dict "dependencies" $.Site.Data.collection.dependencies "category" $category) }}

## {{ $emoji }} {{ $label }}

  {{- range $entry := $entries }}
    {{- $dependency := partial "util/dependency-context.html" $entry }}
    {{- $metadata := $dependency.metadata }}
    {{- $author := print "@" $dependency.authorName }}
    {{- with $dependency.authorLink }}
      {{- $author = print "[@" $dependency.authorName "](" . ")" }}
    {{- end }}
    {{- $item := print "[" $metadata.name "](" $entry.repo ") by " $author }}
    {{- if $dependency.isWorkInProgress }}
      {{- $item = print $item " ⚠️" }}
    {{- end }}
    {{- if $dependency.hasHardwareExtension }}
      {{- $item = print $item " 🪛" }}
    {{- end }}
    {{- $item = print $item ": " $metadata.description }}
    {{- if $dependency.isDeprecated }}
      {{- $item = print "~~" $item "~~ **DEPRECATED**" }}
    {{- end }}
    {{- print "\n* " $item }}
  {{- end }}
{{- end }}

## Community TF image

The community TF image packages pre-built software from this collection on a ready-to-use ZealFS v2 image. See the [latest release](https://github.com/Zeal8bit/Zeal-Software-Collection/releases/latest) for downloads, installation instructions, and included software.

For local builds and image-generation details, see [COMMUNITY_IMAGE.md](COMMUNITY_IMAGE.md).

### Flash a kernel from Zeal

The TF image includes these files under `t:/sys`:

| File | Purpose |
| --- | --- |
| `flasher` | Program the computer's NOR flash. |
| `zeal8bit.rom` | Complete stock kernel ROM image, including its init program. |
| `zshell.rom` | Complete ZShell kernel ROM image, including ZShell and coreutils. |

To install the ZShell ROM, run:

```text
t:/sys/flasher t:/sys/zshell.rom
```

Use `t:/sys/zeal8bit.rom` instead to install the stock ROM. Leave the optional address blank: both images start at address `0`. Flasher erases the NOR flash, writes the selected ROM image, and reboots.

## Add your project to the list

To add your project to the list above, feel free to open a pull request to update the `collection.yml` file.
