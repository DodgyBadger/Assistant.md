# `web_crawl`

## Purpose

Explore related pages across a website using the crawl strategy selected in System settings.

## Arguments

- `url`: starting URL
- `instructions`: description of relevant content
- `max_depth`: link depth, from 1 to 5
- `max_pages`: total page limit, from 1 to 50
- `allow_external`: whether other domains may be followed

## Example

```python
web_crawl(
    url="https://example.com/docs",
    instructions="Find installation and configuration documentation",
    max_depth=1,
    max_pages=5,
)
```

Start with a small page limit. Content is wrapped as untrusted web data. The configured strategy is authoritative and provider failures do not invoke a different crawler automatically.
