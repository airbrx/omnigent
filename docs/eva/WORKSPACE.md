# Eva's workspace

Design approved by Abram on 2026-09-24. This page is what gets built, and why
it is shaped this way.

## Two surfaces, two jobs

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

The coordinator never talks to the outreach app. For a binding on an execution
host's loopback it cannot, and it should not need to.

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
