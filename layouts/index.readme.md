# Zeal 8-bit Computer software collection

In this repository, you will find a list of existing software for Zeal 8-bit Computer.

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
    {{ print "* " $item }}
  {{- end }}
{{- end }}

## Add your project to the list

To add your project to the list above, feel free to open a pull request to update the `collection.yml` file.
