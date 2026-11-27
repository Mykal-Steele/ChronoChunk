# Uniloom in ChronoChunk

Goal: someone can tell the bot "make a slice for the login page under TG-4" or "what's left on my board" and it does it in Uniloom as them, with their own permissions, the way Claude Code does with the Uniloom MCP.

Status: built in 3.32.0. Researched 2026-11-19 from the Uniloom and uniAuth source in `~/UniCorp` and their live discovery documents.

## How Uniloom's MCP signs people in

- `https://loom.psstee.me/mcp` takes `Authorization: Bearer <jwt>`. A request with no token gets a 401 that points at `/.well-known/oauth-protected-resource/mcp` (RFC 9728), which names uniAuth (`https://auth.psstee.me/api/auth`) as the sign-in server.
- uniAuth is a normal OAuth server: authorization code with PKCE (S256 only), refresh tokens with the `offline_access` scope, dynamic client registration (RFC 7591) and client ID metadata documents, token revocation. There is no device-code flow, so signing in needs a browser link that redirects back to something ChronoChunk can receive.
- Access tokens last 15 minutes and refresh tokens 30 days (Better Auth's default; uniAuth does not set it).
- Uniloom gives a token exactly its person's project role (ADR-0012). There are no scopes, so a connected bot can do whatever that person can do on the website, except moving a ticket to Ready or Done, which no agent can.
- A person who never signed in to the Uniloom website, or has not accepted the Unicorp terms, gets refused even with a valid token. The bot passes that message on.
- Every change Uniloom records names the OAuth client from the token, so its activity log will say "ChronoChunk".

Nothing has to change in Uniloom or uniAuth. Dynamic registration already defaults new clients to Uniloom's `/mcp` as the audience.

## The design

### 1. Each person connects their own account

- Asking for something in Uniloom before connecting gets a short reply with a **Connect Uniloom** button that only the asker can press. Pressing it gives the same private link as `/uniloom connect`, and the bot keeps the request for 10 minutes. Once they connect, it answers it without being asked again.
- `/uniloom connect` answers ephemerally (only the person sees it) with a sign-in link. They sign in to uniAuth and approve ChronoChunk on its consent screen. The browser lands on ChronoChunk's callback page, which says "connected, go back to Discord", and the bot tells them "connected to Uniloom as <name>" in a message only they see.
- `/uniloom status` shows who they are connected as. `/uniloom disconnect` revokes the refresh token at uniAuth and deletes it. `/forget` does the same.
- The link carries a one-time `state` that ties it to the Discord user who asked, expires after 10 minutes and works once. The PKCE verifier never leaves the bot.
- If someone forwards their link and a different person signs in with it, the token belongs to the Discord user who asked. Saying "connected as <name>" to them makes that visible straight away.

### 2. Tokens stay with that person

- A new table `uniloom_accounts`: Discord user id, uniAuth `sub`, the display name, the refresh and access tokens encrypted with a key file (like `state/reply_log.key`), the access token's expiry, and when it was connected and last used.
- The bot only uses the token of the person whose message it is answering. If Mint asks "what's on Slade's board", the bot answers with Mint's access, never Slade's, even in the same channel.
- Tokens never go into prompts, logs, the debug log or the reply log. R2 backups already hold the database encrypted, and the tokens are encrypted again on top of that.
- Refreshing happens one at a time per person (an asyncio lock). uniAuth rotates the refresh token on every use, gives the new one another 30 days, and cancels the whole sign-in when an old one comes back (Better Auth 1.7.6, checked in its source), so two replies refreshing at once would end it.
- When the refresh token expires (30 days without use) or was revoked, the account is marked disconnected and the bot says "ur Uniloom sign-in ran out, run `/uniloom connect` again".

### 3. Talking to the MCP

- The official MCP Python SDK (`mcp` 2.3.0, checked on Python 3.15) makes the calls, with the person's access token as a bearer header. It adds about 28 MB once loaded, so it is only imported when someone uses Uniloom.
- The OAuth part is the bot's own (`src/uniloom/oauth.py`). The SDK's `OAuthClientProvider` was the plan, but it is built for one person on one machine: it asks for every scope uniAuth lists (Unishare's `posts:write` and `boards:write` too), a token loaded from storage loses its expiry, and after a restart it guesses the token URL and starts a new sign-in instead of refreshing. The bot follows the same specs itself: RFC 9728 and 8414 discovery with both documents checked, PKCE S256, `state`, the RFC 9207 `iss` check, RFC 8707 `resource`, an OIDC `nonce` checked in the ID token, refresh with rotation, RFC 7009 revocation.
- The bot is a public client named by a Client ID Metadata Document at `https://chrono.oakar.dev/oauth/client.json`, the MCP spec's preferred way: nothing is registered and there is no client secret to keep. It asks only for `openid profile offline_access`.

### 4. Where the callback lands

uniAuth only redirects to URLs registered for the client, and the VM has no HTTPS. Choose one:

| Option | What it needs | Trade-off |
|---|---|---|
| Cloudflare Tunnel to a small aiohttp page in the bot (chosen: `chrono.oakar.dev`) | A domain on Cloudflare and `cloudflared` on the VM | No open ports, real HTTPS, works on phones |
| Cloudflare Worker on `workers.dev` | A Worker that keeps the code in KV for a minute, which the bot fetches with a shared secret | No domain needed, but one more service and one more secret |
| Paste the redirect | Nothing hosted. The redirect fails and the person copies the address bar into a Discord form | Clunky, and bad on phones |

### 5. Using it as well as Claude Code does

Claude Code doesn't load every tool's full description into every message. It knows the names, loads the schemas when a task needs them, follows the server's instructions, calls tools in parallel and runs many steps. ChronoChunk does the same:

- **Offered only when it fits.** A person who isn't connected never sees Uniloom tools. For a connected person, each reply carries one small `uniloom` tool (about 100 tokens) that loads the rest. A message that names Uniloom or has a ticket key like `TG-12` loads the tools straight away.
- **Loaded once, then cached.** `tools/list` and the server instructions (16 tools, about 3 KB of instructions) are fetched once and cached for a few hours, since they are the same for everyone. When the model calls a tool name that no longer exists, the cache refreshes.
- **Schemas made Gemini-safe.** Uniloom's schemas use nullable fields (for example `sourceProposalId: null` clears it). Gemini accepts less JSON Schema than Claude, so each schema goes through a converter, and a unit test runs all 16 schemas through it.
- **Enough steps.** A normal chat reply gets 3 tool rounds, which is too few for "find the project, list its items, read one, then save". A Uniloom reply gets 8, like course mode, and parallel calls stay on.
- **The stronger model.** A reply that uses Uniloom tools runs on `AI_HEAVY_MODEL`, because planning tickets and writing done-when checklists is where the cheap model goes wrong.
- **Paid from the Unicorp allowance.** With `UNILOOM_GEMINI_PROXY_URL` and `UNILOOM_GEMINI_PROXY_KEY` set, every model call after the Uniloom tools load goes to the Unicorp Gemini proxy (`~/unicorp-gemini`), which has its own spending cap. The proxy speaks Gemini's own API, so `src/gemini_proxy.py` translates the bot's OpenAI-style calls. When the proxy's own rules refuse a call (bad key, used up, paused, a model it does not list, its daily cap or too many at once), that call falls back to the bot's own model. Errors from Vertex itself still fail the reply.
- **The right guidance.** The prompt gets Uniloom's own server instructions plus a short version of its skill (`uniloom/skill/SKILL.md`): keys not ids, 3 to 6 done-when items, keep specs short.
- **Short results.** Tool results longer than about 6,000 characters are cut, so a long SPEC body can't use up the reply budget.

### 6. Changes need the person's OK

Ticket text, documents and other people's channel messages can all hold instructions aimed at the bot. A write therefore never happens on the model's word alone:

- Read tools (`list_*`, `get_*`) run straight away.
- Write tools (`save_item`, `set_criteria`, `check_criterion`, `save_document`, `link_document`, `unlink_document`, `remove_blocker`) show what they will do ("create slice 'Login page' under TG-4 with 4 done-when items") with **do it** and **cancel** buttons. Only the connected person can press them, and the buttons stop working after 10 minutes.
- `delete_item` always asks, with the ticket's title in the question.
- Later, `/uniloom auto-confirm` could let a person skip the buttons for small writes like ticking a criterion.

### 7. Where it can be used

Results show up wherever the person asks. Uniloom projects can be private, though, and a server channel is visible to everyone in it. Choose one:

- Anywhere, and the person decides what to ask in public (chosen).
- DMs only.
- Reads anywhere, writes only in DMs.

A bot reply in a server channel goes into the channel log and that night's summary like any other reply. Replies in DMs are not saved.

## Build order

| # | Step | Done when |
|---|---|---|
| 1 | Spike | The MCP SDK runs on Python 3.15 next to discord.py, and a script connects one account through the chosen callback and lists projects |
| 2 | Accounts | `uniloom_accounts` with encrypted tokens, `/uniloom connect`, `status` and `disconnect`, `/forget` revokes, refresh with a per-person lock |
| 3 | Read tools | Cached `tools/list`, the Gemini schema converter, the `uniloom` loader tool, 8 rounds and the heavy model for Uniloom replies |
| 4 | Writes | Confirm buttons for every write, `delete_item` always asks |
| 5 | Polish | `/help` topic, PRIVACY.md section, live eval: plan a feature with slices and checklists in a test project |

## Decisions (2026-11-20)

| Question | Answer |
|---|---|
| Where the callback lands | Cloudflare Tunnel at `chrono.oakar.dev` (the domain is on Cloudflare, the name is free) |
| Where it can be used | Anywhere. Only the connected person can make the bot use their account, and showing it in channels is on purpose, so others see what is going on in Uniloom |
| Writes | Always ask with buttons. `/uniloom auto-confirm` comes later and lets a person skip them for small changes |
