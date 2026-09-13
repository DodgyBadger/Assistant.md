# `file_write`

## Purpose

Create, append, edit, move, delete, and create folders in the current vault.

## Operations

- `write`
- `append`
- `edit_line`
- `replace_text`
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

`move` does not replace an existing destination unless `overwrite=True` is explicitly provided.

```python
file_write(operation="delete", path="Scratch.md", confirm_path="Scratch.md")
```

## Notes

- Paths are vault-relative.
- Mutating operations cannot write to virtual mounts.
- Use `replace_text` for exact in-file edits.
- Use `edit_line` when the line number and existing line content are both known.
- Clear an existing file with `write(overwrite=True, content="")`; there is no separate truncate operation.
- Use `file_read` before calling `file_write` when you need to inspect context.
- For multiple independent changes, issue separate `file_write` calls in one response. Inline edit mode can then review and return each operation independently.
- Mutations targeting the same file are serialized, so independent text edits issued together cannot overwrite one another with stale full-file content.
- Sequence dependent mutations across turns, or use a bounded script when deterministic ordered processing is required.
