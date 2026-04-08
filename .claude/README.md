To install skills, copy the files from `.claude/skills/` to `~/.claude/skills/`. They can then be triggered as slash commands in Claude Code.

## /db

Executes natural-language queries against the local MariaDB MCP server. It automatically resolves which database(s) each table lives in using `~/.claude/db-schema.json`, writes fully-qualified SQL, asks for confirmation before running, and displays results.

Claude may ask your permission to start the MCP server, stop the MCP server, query the DB, use a bash command, or possibly some additional things like using python to format a pretty output. Ensure you know what is happening for each permission ask.

**Environment** — defaults to `dev`. Prefix with the environment name to target another:

```
/db how many guests have no ticket?
/db staging how many guests have no ticket?
/db production how many BPEs in datalake need to be synced?
```

Valid environments: `dev`, `staging`, `production`.

**Additional Examples:**

```
/db how many guests have an ambassador that does not have a ticket to the event they are an ambassador for?

/db staging how many LMS courses have we synced from Absorb?

/db for each organization that ran an event in the last 30 days, show total donation revenue and number of unique donors, ordered by revenue desc

/db production find all salesforce IDs for BPEs in datalake that failed to sync and do not have an organizationID on the bpe_status table
```

**Advanced — pairing with code context:**

You can reference code directly and ask Claude to find real data that exercises it. This is useful for debugging, validating edge cases, or understanding real-world impact of a code path.

```
This function handles guest check-in when no ticket is found. Use /db to find guests in production that would hit this branch — i.e. guests with a check-in attempt but no associated ticket.

---

I'm seeing an error in this payment processing path when org_payment_consent is missing. Use /db staging to find payments made in the last 7 days for orgs that have no row in org_payment_consent.

---

This code skips sending a welcome message if the guest already has a message_history entry. Use /db to find guests created in the last 24 hours who did not receive a welcome message, and check whether they all have a pre-existing message_history row.
```

If you have enabled the Jira connection:
```
Check Jira ticket ABC-1234. Gather additional relevant information to help debug the issue by using /db for the affected entities (org, event, guests, etc.).
```

## /db-refresh-schema

Rebuilds `~/.claude/db-schema.json` by querying `information_schema` for all databases and tables. Run this when new services are deployed or tables are added.

The manifest stores bare base names (e.g. `phaas-ticket-api`) without an environment prefix, so it works across all environments.

**Customize the manifest after generating:**

- `aliases` — teach Claude that "event" means the `virtual_event` table, etc.
- `collision_defaults` — resolve ambiguity for tables that exist in multiple databases. For example, `"raffle ticket"` maps to `phaas-raffle-api`.`ticket` rather than `phaas-ticket-api`.`ticket`.

The manifest is generated automatically on the first `/db` invocation if it doesn't exist.
