# 0043 - Protect Application Ingress With Deployment Authentication

## Status

Accepted.

## Context

The advanced shell selected in ADR 0042 is intentionally capable of running
arbitrary software and making network requests. It shares a private Docker
network with AssistantMD so the application can reach its fixed SSH endpoint.
Network isolation alone cannot make that relationship one-way: code in the
advanced shell can also route requests to AssistantMD.

Historically, reaching the AssistantMD HTTP service was sufficient to use its
UI and complete API. Under that contract, separating shell execution from the
application process would protect some filesystem and process state, but an
agent could regain most application authority by calling the unprotected API.
That would substantially undermine the reason to keep shell execution outside
AssistantMD; from an authority perspective, placing shell access directly in
the application would be little different.

AssistantMD must also support several deployment shapes without imposing a
second human login behind an existing authenticating proxy. Transport
authentication must remain distinct from the principal and execution-authority
model established by ADR 0028.

## Decision

Wrap the complete ASGI application in a default-deny authentication boundary.
Protect UI routes, APIs, static assets, streams, uploads, downloads, generated
API documentation, and future protocol surfaces unless a small typed route
classification explicitly makes a method and path public.

Require the deployment to select exactly one authentication mode:

- `disabled` deliberately admits every routable peer to the complete
  application as `local-user`. It exists for recovery, testing, and explicit
  experimentation and is reported as unprotected.
- `loopback` admits only an immediate IPv4 or IPv6 loopback socket peer without
  a login. Forwarded headers do not influence the decision. This primarily
  serves direct host development rather than the standard bridged Compose
  deployment.
- `trusted_proxy` requires a cryptographically random private assertion that an
  authenticating reverse proxy removes from inbound requests and injects
  upstream. It reuses the proxy's human session and does not add an AssistantMD
  login.
- `owner_token` accepts a deployment-owned bearer credential for programmatic
  clients and exchanges it for a bounded browser session with CSRF protection.

Load the selected mode and its credential from restart-bound deployment
configuration, not user-editable application settings or the principal-owned
encrypted secrets store. Missing or invalid authenticated-mode configuration
fails closed. Authentication credentials must not enter model context, tool
arguments or results, chat state, application APIs, the advanced shell,
observability data, or persisted diagnostics.

Treat authentication as proof that a request may resolve to a principal, not as
principal authority itself. After successful authentication, install the
principal's existing `ExecutionAuthority`; never use proxy assertions, owner
tokens, sessions, or CSRF values as resource ownership identifiers or agent
credentials. Initially, authenticated interactive requests resolve to the
single `local-user` principal.

## Consequences

- Network reachability from the advanced shell or another container no longer
  grants AssistantMD authority in an authenticated mode.
- The separate advanced-shell container becomes a meaningful application
  authority boundary as well as a filesystem and process boundary.
- Deployments behind an existing authenticating proxy avoid a redundant second
  login while AssistantMD still verifies that requests traversed that proxy.
- Deployments without such a proxy can use AssistantMD's owner session over a
  separately provided TLS endpoint.
- `disabled` mode intentionally permits advanced-shell control of AssistantMD
  when routing allows it; the product reports rather than overrides that choice.
- New public endpoints require explicit security review and route-inventory
  coverage.
- A future multiuser authenticator can resolve different principals without
  changing the downstream authority and ownership model.

## Evidence

- Authentication policy and middleware: `core/authentication/`
- Browser session boundary: `api/authentication.py`
- Execution-principal model: ADR 0028
- Advanced execution boundary: ADR 0042
- Security and deployment contract: `docs/setup/security.md` and
  `docs/setup/installation.md`
- Implementation and adversarial validation record:
  `API_OWNER_AUTHENTICATION_IMPLEMENTATION_PLAN.md`
