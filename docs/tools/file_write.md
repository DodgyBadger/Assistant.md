# `file_write`

## Purpose

Create and edit plain UTF-8 text; copy, move, and delete files of any type; create folders.

## Operations

- `write`
- `append`
- `edit_line`
- `replace_text`
- `copy`
- `move`
- `delete`
- `mkdir`

## Examples

```python
file_write(operation="write", path="notes/new.md", content="# Draft\n")
```

`write` is create-only by default. If the file already exists, it returns `status: "already_exists"` and does not overwrite.

When the user asks to update or rewrite a file that has already been read or is otherwise known to exist, set `overwrite=True` on the first call. Do not first attempt a create-only write.

```python
file_write(
    operation="write",
    path="notes/existing.md",
    content="# Replacement\n",
    overwrite=True,
)
```

Use `overwrite=True` only when the user intent to replace the full file is clear.

```python
file_write(operation="append", path="notes/log.md", content="\n- New item")
```

```python
file_write(
    operation="edit_line",
    path="notes/todo.md",
    line_number=5,
    old_text="- [ ] draft",
    new_text="- [x] draft",
)
```

```python
file_write(
    operation="replace_text",
    path="notes/todo.md",
    old_text="- [ ] draft",
    new_text="- [x] draft",
)
```

```python
file_write(operation="move", path="Draft.md", destination="Archive/Draft.md")
```

```python
file_write(operation="copy", path="Project/source.docx", destination="AssistantMD/shell-exchange/source.docx")
```

`copy` supports files only. `copy` and `move` replace an existing destination only with `overwrite=True`.

```python
file_write(operation="delete", path="Scratch.md")
file_write(operation="delete", paths=["Scratch.md", "Archive/old-project"])
```

Use `path` for one delete target or `paths` for several, never both. Deleting a folder recursively removes all its contents. Duplicate targets and children of selected folders are collapsed. Invalid, missing, or protected targets reject the whole selection.

## Notes

- Paths are vault-relative exact filenames; no extension is inferred.
- `delete` cannot target the vault root, including aliases such as `.` and `./`.
- Mutating operations cannot write to virtual mounts.
- Use `replace_text` for exact in-file edits.
- Use `edit_line` when the line number and existing line content are both known.
- Clear an existing file with `write(overwrite=True, content="")`; there is no separate truncate operation.
- Use `file_read` before calling `file_write` when you need to inspect context.
- Use `paths` for one deletion selection. Use separate `file_write` calls for other independent mutations.
- Mutations targeting the same file are serialized, so independent text edits issued together cannot overwrite one another with stale full-file content.
- Sequence dependent mutations across turns, or use a bounded script when deterministic ordered processing is required.
