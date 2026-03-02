# Contributing

This repository uses [Hugo](https://gohugo.io/) to generate:

- `_site/index.html`
- `_site/README.md`

The source of truth for the collection is [`collection.yml`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/collection.yml).

## Adding a Project

To add a project to the collection, edit [`collection.yml`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/collection.yml) and add a new item under `dependencies:`.

At minimum, each entry should include:

```yaml
- id: your-user/your-repo
  repo: https://github.com/your-user/your-repo.git
  metadata:
    name: Your Project Name
    category:
      - software
    description: Short description of the project
    author:
      name: your-user
      link: https://github.com/your-user
```

Required fields:

- `id`: unique identifier for the project
- `repo`: cloneable repository URL
- `metadata.name`: human-readable project name
- `metadata.category`: one or more categories
- `metadata.description`: short summary
- `metadata.author.name`: author name

Recommended fields:

- `metadata.author.link`: author profile URL
- `aliases`: alternative names or short forms

Optional fields:

- `branch`: if the project should use a non-default branch
- `tag`: if the project should point to a specific tag
- `build`: build metadata
- `depends_on`: dependency IDs from other entries
- `metadata.flags`: status flags

### Valid Categories

Current category values used by the site:

- `game`
- `demo`
- `software`
- `library`
- `language`
- `development-tool`
- `service`
- `core`
- `extra`
- `hardware`

### Optional Flags

Current flags recognized by the site:

- `work_in_progress`
- `hardware_extension`
- `deprecated`

Example:

```yaml
- id: your-user/your-repo
  repo: https://github.com/your-user/your-repo.git
  metadata:
    name: Your Project Name
    category:
      - software
      - library
    description: Short description of the project
    author:
      name: your-user
      link: https://github.com/your-user
    flags:
      - work_in_progress
  aliases:
    - your-project
```

## Before Opening a Pull Request

Please:

1. Check that your YAML formatting is valid and list items are indented consistently.
2. Make sure the project metadata is complete and uses the correct category and flags.
3. Keep descriptions short and factual.

## Local Testing And Site Updates

If you are only adding a project entry, you usually only need to edit `collection.yml`.

If you want to test locally, or if you are changing templates, styles, or JavaScript, use Hugo locally.

### Prerequisites

You need:

- Hugo extended

This project currently builds with Hugo `v0.157.0+extended`.

To verify your installation:

```sh
hugo version
```

### Local Development

Run the local dev server:

```sh
hugo server
```

That will:

- watch files for changes
- rebuild automatically
- live-reload in the browser

By default, Hugo serves the site at:

```text
http://127.0.0.1:1313
```

If you want slower but fuller rebuilds while editing templates/assets:

```sh
hugo server --disableFastRender
```

### Build

To generate the site into `_site/`:

```sh
hugo
```

Or use the Makefile target:

```sh
make
```

`make` runs Hugo and then copies `_site/README.md` to the repository root as `README.md`.

To remove generated output:

```sh
make clean
```

## Project Structure

Important files and directories:

- [`collection.yml`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/collection.yml): the collection data
- [`layouts/`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/layouts): Hugo templates
- [`assets/`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/assets): SCSS, JS, and bundled font assets
- [`_site/`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/_site): generated output
- [`resources/_gen/`](/Users/david.higgins@konghq.com/Documents/Private/zeal/zeal8bit/Zeal-Software-Collection/resources/_gen): Hugo asset cache

## Notes

- This repository is generated from `collection.yml`; avoid editing `_site/` by hand.
- If you change templates, SCSS, or JS, Hugo will rebuild the derived assets automatically.
