# Eva's workspace

Design approved by Abram on 2026-09-24. This page is what gets built, and why
it is shaped this way.

## One surface, as of 2026-09-26

**Abram changed the design on 2026-09-26: Eva runs entirely inside
`https://omnigent.airbrx.ai` as one workspace, like Iris.** Her chat and the
outreach app's management tabs (leads, pool, accounts, analytics, guardrails,
sync, settings) are in one place. There is no `outreach.airbrx.ai`, no DNS
record, and no second Google sign-in.

**Why it changed.** The 2026-09-24 design below had two surfaces for one
reason: the coordinator could not reach an outreach app on another machine's
loopback, so the app had to be a separate place a rep went to. The outreach
app now runs on the **same box** as the Omnigent server, bound to
`127.0.0.1:8000`, and that removes the reason. Omnigent reverse-proxies it at
`/eva/app` and tells it who the caller is with a signed header, so the rep's
Omnigent sign-in is the only sign-in.

What this supersedes below: "Two surfaces, two jobs" and every "links to the
outreach app at the binding's `base_url`". The workspace links to `/eva/app`
instead, and the adapter's `outreach_url` follows with the adapter change. The
binding's `base_url` is unchanged and still means the app as Eva's MCP client
sees it.

## Identity contract v1.1: Omnigent to outreach

This text is shared, identically, with the outreach app and the frontend.
Neither side reinterprets it. v1.1 (2026-09-26) amends only the forwarding
rules for `Host` and `X-Forwarded-*`; everything else is v1.

- Omnigent reverse-proxies `https://omnigent.airbrx.ai/eva/app/<path>` to
  `http://127.0.0.1:8000/<path>`, STRIPPING the `/eva/app` prefix.
- Before forwarding it deletes every inbound header whose name starts with
  `X-Omnigent-` (case-insensitive), then sets:
  - `X-Omnigent-User-Email`: signed-in user's email, lowercased
  - `X-Omnigent-Timestamp`: unix seconds, integer
  - `X-Omnigent-Signature`: lowercase hex HMAC-SHA256, key
    `OUTREACH_IDENTITY_SECRET`, message = email + "\n" + timestamp + "\n" +
    METHOD + "\n" + path_after_prefix_including_query
- (v1.1) It PRESERVES the `Host` header as the browser sent it
  (`omnigent.airbrx.ai`), so the app's `url_for` builds public URLs, and sets
  `X-Forwarded-Proto: https`. It DELETES every inbound `X-Forwarded-*` header
  from the client, in addition to `X-Omnigent-*`, and never sets
  `X-Forwarded-For`. Why: the app runs on `127.0.0.1:8000` with
  `uvicorn --proxy-headers --forwarded-allow-ips 127.0.0.1`, so a forwarded
  client `X-Forwarded-For` would become the app's peer address and its
  loopback check could be spoofed with one header.
- No signed-in Omnigent user: Omnigent answers 401 itself and never forwards.
- Outreach, in `OUTREACH_AUTH_MODE=omnigent`, runs with root path `/eva/app`.
  It accepts identity only from a loopback peer, with a constant-time
  signature check and |now - timestamp| <= 60s. It maps the email to an ACTIVE
  rep; unknown or inactive gets 403, anything else 401, with no fallback to
  another mode. It refuses to start if the secret is unset or under 32 bytes.
  It renders without its own top nav and sends
  `Content-Security-Policy: frame-ancestors 'self'`.
- MCP is unchanged: Eva calls `http://127.0.0.1:8000/mcp` directly with the
  rep's own bearer token.
- Test vector, which both sides reproduce: secret
  `test-secret-0123456789abcdef0123456789abcdef`, email
  `aerickson@airbrx.com`, timestamp `1790000000`, method `GET`, path
  `/leads?page=2`, signature
  `e21566ae5280a70c8f7ba6b55e6c11b2465634d6b101c67ee5a844f4a12ecaad`.

### How Omnigent's side honours it

`omnigent/airbrx/eva/proxy.py`, tested in `tests/airbrx/test_eva_app_proxy.py`.

**Where the email comes from.** Omnigent's own authenticated identity, the
user id its auth provider resolves, and nothing the client sends. On
`omnigent.airbrx.ai` the provider is `oidc` against JumpCloud: the callback
takes the id_token's `email` claim only when the IdP marks it verified,
lowercases it, checks the domain allow list (`airbrx.com`, `airbrx.ai`), and
mints the session cookie with that email as its subject. So the user id *is*
the verified, lowercased email. The proxy forwards it only when the auth
source is `oidc` or `header` (a trusted SSO proxy) and the id is shaped like an
address. `accounts` mode (usernames an admin typed), the single-user `local`
identity, and anything not shaped like an email get **403** and are not
forwarded. There is no mapping table: whether the email is a rep is the
outreach app's decision, against its `reps` table.

**Readings the contract leaves implicit, stated so both sides agree:**

- *path_after_prefix_including_query* is the raw request target as sent on
  the wire, percent-encoding untouched: the path after `/eva/app`, then `?`
  and the raw query string when there is one. `/eva/app` and `/eva/app/` both
  forward, and sign, as `/`.
- A target with a `.` or `..` segment (encoded or not) is refused with 400,
  not resolved. The HTTP client would collapse it, so `/eva/app/x/../mcp`
  would otherwise reach `/mcp`, and the signed path would not be the path
  sent.
- `/mcp` is refused with 404 however it is spelt: `/eva/app/mcp`, `/MCP`,
  `/%6Dcp`, `//mcp`, `/%2Fmcp`. `/mcp-help` is forwarded.

**Beyond the contract, all on the forwarding side:**

- The secret is read from the environment per request. Unset, or under 32
  bytes, and `/eva/app` answers **503** and forwards nothing; the server does
  not refuse to start, because a coordinator crash loop takes Iris down too.
- Omnigent's own credentials are not forwarded: `Authorization` is dropped,
  and Omnigent's cookies (`__Host-ap_session`, `ap_auth_state` and the rest of
  the `ap_` family) are removed from `Cookie`. Every other cookie passes.
- `Forwarded` and `X-Real-IP` are dropped along with `X-Forwarded-*`, for
  the same reason as v1.1's rule, and so is Omnigent's own auth header.
  The only forwarding header the app receives is `X-Forwarded-Proto: https`.
- Hop-by-hop headers, and any named in `Connection`, are stripped both ways.
  Status codes, `Location` (never followed) and every `Set-Cookie` pass
  through untouched. Responses stream.
- Request bodies are capped at 10 MiB (413). Upstream timeouts: 5s connect,
  60s read, 30s write. Unreachable is 502, a timeout 504.
- WebSockets are not proxied.

## What the workspace UI can rely on from the backend

For the frontend (`omnigent/airbrx/eva/ui/`, owned by the UI branch):

- **The outreach app's base is `/eva/app`,** same origin as the workspace.
  Every management tab is a page under it: `/eva/app/`, `/eva/app/leads`,
  and so on, exactly the outreach app's own routes with `/eva/app` in front.
  Frame or link them directly; the rep's Omnigent session cookie is what
  authenticates them, so nothing needs a token or a second sign-in. The app
  sends `Content-Security-Policy: frame-ancestors 'self'`, so it frames inside
  Omnigent and nowhere else.
- **Status codes mean:** 401 not signed in to Omnigent; 403 either a sign-in
  with no verified email (from Omnigent) or not an active rep (from the app);
  503 the server is missing `OUTREACH_IDENTITY_SECRET`; 502 or 504 the app is
  down or slow. None of these is worth retrying automatically.
- **Never `/eva/app/mcp`.** It is always 404.
- **The adapter API** is `/v1/eva/sessions/{session_id}/ui/api/{chat,cancel,state,refresh,readiness}`.
  Its alignment with Iris's shapes is a separate change; the table of
  differences lands with it, in the section below this one.

## Two surfaces, two jobs (superseded 2026-09-26, see above)

**The Omnigent workspace is the everyday surface.** A rep picks Eva in the
agent drawer, lands in her Airbrx-branded workspace, sees the pipeline, and
gets work done with her: claim a lead, qualify it, draft the first touch,
check the guardrails, ask for approval.

**The outreach app is the deeper surface.** It is where Eva's data and
configuration live: lead edits, visibility, approvals, the guardrail rules,
MCP tokens. The workspace does not rebuild any of that. It links to the
outreach app for it, at the binding's `base_url`.

## Shape, and why it copies Iris

Iris's workspace is a small branded web app, served by Omnigent per session and
framed by the Omnigent web app at `/iris/:sessionId`. Eva's is the same shape:

| Piece | Iris | Eva |
|---|---|---|
| Web route | `/iris`, `/iris/:sessionId` | `/eva`, `/eva/:sessionId` |
| Landing | tenants ranked by cache misses | Eva's bindings, with a start button |
| Framed app | vendored in `iris-source.zip` | `omnigent/airbrx/eva/ui/`, tracked in git |
| Adapter API | `/v1/iris/sessions/{id}/ui/api/{chat,cancel,state,refresh,readiness}` | the same five under `/v1/eva/...` |
| Drawer | "Open Iris workspace" | "Open Eva workspace" |

## Where the data comes from

The adapter routes never talk to the outreach app. For a binding on an
execution host's loopback they cannot, and they should not need to. (Since
2026-09-26 the coordinator does talk to it, but only as the `/eva/app` proxy,
on behalf of a signed-in rep; the workspace state is still built as below.)

**State is assembled from Eva's own tool results already recorded in the
session, with no model call.** Those results are the newest `list_pool` and
`list_my_leads`, `get_lead` per lead with profile, qualification, touches and
drafts, and `submit_draft` and `request_approval` results with their rule
results. A later result replaces an earlier one for the same lead or draft.

**Refresh runs one Eva turn.** It asks her to call `list_pool`, `list_my_leads`,
and `get_lead` with drafts for every lead she holds, and to write nothing.

Until a refresh has run the workspace is empty and says so. It never shows
sample data; Iris learned that the hard way.

## Screens

- **Pipeline.** The pool and your leads, with claim days remaining.
- **Lead.** Profile, qualification, touches, drafts, and one-click asks: claim,
  qualify, draft a first touch, request approval. Each ask is an ordinary chat
  message, so it is visible and editable in the conversation.
- **Drafts and approvals.** Every draft Eva has seen: status, version, subject,
  the guardrail results with their severity, and whether it is waiting on
  approval. Approve and mark sent are not buttons. The workspace names who must
  do them and links to the lead in the outreach app.
- **Chat.** Beside everything else, as in Iris.

## Boundaries

- Every adapter route checks that the caller is authenticated, that the session
  is Eva's, and that the caller's binding authorizes them.
- A recorded turn containing any tool outside Eva's allow list, plus
  `ToolSearch`, is rejected, as Iris's are.
- The workspace cannot approve a draft or mark it sent. Eva cannot either.
- No em dashes in any copy.

## Binding change

Creating a session on a host needs an absolute working directory for the
runner. Eva's binding gains an optional `workspace`, the same field Iris's
has. Without it the workspace says the binding needs one rather than failing
on the first click.
