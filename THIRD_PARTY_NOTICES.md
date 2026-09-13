# Third-Party Notices

This project includes and depends on third-party open-source software.

## Bundled Frontend Assets (Redistributed in-repo)

- **MathJax** (`static/vendor/mathjax`) — Apache-2.0  
  License file: `static/vendor/mathjax/LICENSE`
- **marked** (`static/vendor/marked.min.js`) — MIT  
  License header is embedded at the top of the vendored file.
- **DOMPurify** (`static/vendor/dompurify.min.js`) — MPL-2.0 OR Apache-2.0

## Frontend Build Dependencies

- **tailwindcss** (`package.json`) — MIT
- **@tailwindcss/typography** (`package.json`) — MIT

## Python Dependencies

Python package dependencies are managed via:

- `docker/pyproject.toml`
- `docker/uv.lock`

## Advanced Shell Runtime

The optional advanced-shell image includes:

- **Python 3.13** — Python Software Foundation License
- **Node.js 24** — MIT License
- **npm** — Artistic License 2.0
- **uv** — Apache-2.0 OR MIT
- Debian Bookworm command-line and OpenSSH packages under their respective
  Debian package licenses

Exact source-image versions and manifest digests are declared in
`docker/advanced-shell/Dockerfile`. A generated image SBOM remains the canonical
full transitive inventory for a released advanced-shell image.

## Full Dependency Inventories

For complete dependency trees and resolved versions, see:

- `package-lock.json` (Node/npm)
- `docker/uv.lock` (Python/uv)
