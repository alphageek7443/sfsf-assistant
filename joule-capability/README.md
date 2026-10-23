# Joule Capability: sfsf-assistant

## Prerequisites

**Joule DTA schema 3.28.0+ required.** Code-based agents (BYOA) using the
`agent-request` action type require Joule DTA schema 3.28.0 or higher.

If deploy fails with "Schema version defined in config file is greater than the
current schema version of Joule", your tenant needs a Joule service update.

## File Structure

```
joule-capability/
├── capability.sapdas.yaml       # Capability metadata (schema 3.28.0)
├── capability_context.yaml      # Multi-turn context variables
├── da.sapdas.yaml               # DA deployment descriptor (schema 1.4.0)
├── functions/
│   └── call_agent.yaml          # A2A agent-request function
└── scenarios/
    └── invoke_agent.yaml        # User intent → function mapping
```

## Deploy to Joule

1. Ensure the BTP destination `sfsf-assistant-auth` exists and points to:
   `https://your-app.cfapps.<region>.hana.ondemand.com`

2. Install Joule CLI: `npm install -g @sap/joule-cli`

3. Login: `joule login --use-env` (from .env) or `joule login ` (interactive)

4. Compile + deploy: `joule deploy ./da.sapdas.yaml --compile -n "sfsf_assistant_a2a"`
