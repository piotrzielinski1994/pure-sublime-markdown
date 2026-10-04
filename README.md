# Pzielinski Markdown

Sublime Text 4 plugin: VS Code-style Markdown preview with inline HTML
(`<img>`, `<span style="color:...">`) rendered directly in Sublime.

## Features

1. In-place preview (`Ctrl+Shift+V`, like VS Code): the same tab toggles
   between source and rendered Markdown - headings, bold, links,
   colored `✓`/`✗` markers, local images in rows, separator lines.
   Saving the `.md` refreshes the preview.
2. Inline thumbnails under `<img>` lines in the editor.
3. `Alt` + mouse wheel = fast scroll (~10 lines).

## Install

```bash
git clone <this-repo> "Packages/PzielinskiMarkdown"
```

Or copy the files into `Packages/User`. No dependencies, stdlib only.
Requires Sublime Text 4 (build 4073+).

## Commands (Command Palette)

1. `pzielinski-markdown: Preview`
2. `pzielinski-markdown: Toggle inline images`

## Key bindings and mouse (opt-in)

The package ships with no active key bindings. To enable them, uncomment
the lines in `Default (<platform>).sublime-keymap` /
`Default (<platform>).sublime-mousemap`:

1. `Ctrl+Shift+V` - toggle in-place preview (same as VS Code).
2. `Alt` + mouse wheel - fast scroll (~10 lines per tick).

## Settings (`pzielinski_markdown.sublime-settings`)

1. `enabled` - inline images on/off (default `true`).
2. `image_height` - thumbnail height in px (default `150`).
3. `max_images_per_line` - cap per line (default `12`).

## Notes

1. minihtml has no `<hr>` or flexbox: rules render as bordered `div`s,
   images flow inline and wrap.
2. Preview uses a generated color scheme matching your theme with a
   hidden caret; the view is read-only.
