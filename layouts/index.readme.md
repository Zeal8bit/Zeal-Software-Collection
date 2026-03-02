{{- $categories := slice "game" "demo" "software" "library" "language" "development-tool" "service" "core" "extra" "hardware" -}}
{{- $emojis := dict
  "game" "🕹️"
  "demo" "💾"
  "software" "💻"
  "library" "📚"
  "development-tool" "🛠️"
  "language" "🔤"
  "service" "🔧"
  "core" "⚡"
  "extra" "✨"
  "hardware" "⚙️"
-}}
{{- $lines := slice
  "## Zeal 8-bit Computer software collection"
  ""
  "In this repository, you will find a list of existing software for Zeal 8-bit Computer."
  ""
  "Projects marked with:"
  ""
  "* 🪛 involve a hardware extension."
  "* ⚠️ involve a *Work In Progress* state, may not be usable yet."
-}}
{{- range $category := $categories -}}
  {{- $label := printf "%s%s" (upper (substr $category 0 1)) (substr $category 1) -}}
  {{- $lines = $lines | append "" (printf "### %s %s" (index $emojis $category) $label) "" -}}
  {{- range $.Site.Data.collection.dependencies -}}
    {{- $metadata := .metadata -}}
    {{- $flags := default (slice) $metadata.flags -}}
    {{- if in $metadata.category $category -}}
      {{- $author := printf "@%s" $metadata.author.name -}}
      {{- with $metadata.author.link -}}
        {{- $author = printf "[@%s](%s)" $metadata.author.name . -}}
      {{- end -}}
      {{- $item := printf "[%s](%s) by %s" $metadata.name .repo $author -}}
      {{- if in $flags "work_in_progress" -}}
        {{- $item = printf "%s ⚠️" $item -}}
      {{- end -}}
      {{- if in $flags "hardware_extension" -}}
        {{- $item = printf "%s 🪛" $item -}}
      {{- end -}}
      {{- $item = printf "%s: %s" $item $metadata.description -}}
      {{- if in $flags "deprecated" -}}
        {{- $item = printf "~~%s~~ **DEPRECATED**" $item -}}
      {{- end -}}
      {{- $lines = $lines | append (printf "* %s" $item) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- $lines = $lines | append "" "" "## Add your project to the list!" "" "To add your project to the list above, feel free to open a pull request to update the `collection.yml` file." -}}
{{- delimit $lines "\n" -}}
