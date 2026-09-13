# `file_read`

## Purpose

Read, list, search, and inspect markdown frontmatter in the current vault.

## Operations

- `read`
- `list`
- `search`
- `frontmatter`

## Examples

```python
file_read(operation="list")
```

```python
file_read(operation="list", path="projects", recursive=True)
```

```python
file_read(operation="read", path="notes/project.md")
```

```python
file_read(operation="read", path="notes/project.md", start_line=1, line_count=40)
```

```python
file_read(operation="search", path="projects", search_term="TODO")
```

```python
file_read(operation="frontmatter", path="projects", keys="title,status")
```

## Notes

- Paths are vault-relative.
- Use `read` with `start_line` and `line_count` instead of a separate `head` operation.
- Direct image reads and markdown files containing local images return multimodal payloads when image policy permits. Ranged reads return text only.
- `frontmatter` accepts a markdown path, directory, or glob and returns structured `items` metadata. Use `keys` to select a comma-separated subset.
- Use `file_write` for create, edit, move, delete, and mkdir operations.
