# `web_search`

## Purpose

Find relevant web pages using the search strategy selected in System settings.

## Arguments

- `query`: required search query
- `max_results`: maximum results to return, from 1 to 10

## Example

```python
web_search(query="latest postgres release notes", max_results=5)
```

Results contain a title, snippet, and URL and are wrapped as untrusted web data. Use `web_extract` when you need the full content of a known result.

The configured strategy is authoritative. Provider failures are returned with the selected strategy name and do not trigger another provider automatically.
