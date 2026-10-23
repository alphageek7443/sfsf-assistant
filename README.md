# sfsf-assistant

A LangGraph A2A agent template for SAP Joule on BTP Cloud Foundry, powered by SAP GenAI Hub.

Use this as a starting point to build your own SuccessFactors assistant — add tools, customize the system prompt, and deploy to CF with Joule integration out of the box.

## Features

- **A2A protocol** — works as a Joule capability via the Agent-to-Agent standard
- **Principal propagation** — calls SuccessFactors as the logged-in user via BTP Destination Service
- **IAS authentication** — verifies Joule-issued JWT tokens on every request
- **Streaming** — streams intermediate tool-call progress back to the client
- **LangGraph ReAct agent** — tool-calling loop with conversation memory

## Project Structure

```
sfsf-assistant/
├── app/
│   ├── __main__.py          # Server entry point (Starlette + A2A + IAS middleware)
│   ├── agent.py             # LangGraph ReAct agent + GenAI Hub LLM setup
│   ├── agent_executor.py    # A2A protocol bridge
│   ├── agent_card.py        # Agent Card (discovery metadata)
│   └── tools.py             # SuccessFactors OData tools
├── joule-capability/
│   ├── capability.sapdas.yaml       # Capability definition
│   ├── capability_context.yaml      # Multi-turn context variables
│   ├── da.sapdas.yaml               # DA descriptor
│   ├── functions/
│   │   └── call_agent.yaml          # A2A agent-request function
│   └── scenarios/
│       └── invoke_agent.yaml        # User intent mapping
├── manifest.yml             # CF deployment manifest
├── requirements.txt
├── Procfile
└── runtime.txt
```

## Prerequisites

- BTP subaccount with:
  - **AI Core** service instance (GenAI Hub, `gpt-4.1` or similar deployment)
  - **Destination Service** instance
  - **SuccessFactors** system connected via `OAuth2SAMLBearerAssertion` destination named `sfsf-conn`
  - **IAS** tenant for JWT verification
- Cloud Foundry CLI (`cf`)
- Joule CLI (`joule`) for capability deployment

## Deploy to Cloud Foundry

1. Edit `manifest.yml` — set your `IAS_ISSUER`, `IAS_AUDIENCE`, CF route, and destination service name.

2. Bind your destination service:
   ```bash
   cf create-service destination lite your-destination-service
   ```

3. Push:
   ```bash
   cf login -a https://api.cf.<region>.hana.ondemand.com
   cf push
   ```

4. Verify the agent card is reachable:
   ```bash
   curl https://your-app.cfapps.<region>.hana.ondemand.com/.well-known/agent.json
   ```

## Connect to Joule

1. Create a BTP destination named `sfsf-assistant-auth` pointing to your CF app URL with `OAuth2ClientCredentials` authentication (so Joule can call the agent).

2. Deploy the Joule capability:
   ```bash
   cd joule-capability
   joule login
   joule deploy ./da.sapdas.yaml --compile -n "sfsf_assistant_a2a"
   ```

## Principal Propagation Setup

This app uses **principal propagation** — SuccessFactors APIs are called as the logged-in user, not a technical user. The flow:

1. Joule sends the user's IAS JWT in the `Authorization` header
2. The app verifies the token and stores it for the request
3. The BTP Destination Service exchanges it for a SFSF bearer token via `OAuth2SAMLBearerAssertion`

To set this up:
1. Get the BTP subaccount SAML signing certificate
2. Register an OAuth2 client in SuccessFactors Admin Center
3. Configure the `sfsf-conn` destination in BTP with `OAuth2SAMLBearerAssertion`
4. Ensure user ID mapping is set (email-based is most common)

## BTP Destinations

The `destinations/` folder contains ready-to-import `.properties` files for the two BTP destinations required by this app:

| File | Destination | Purpose |
|------|-------------|---------|
| `sfsf-conn.properties` | `sfsf-conn` | Connects to SuccessFactors with `OAuth2SAMLBearerAssertion` for principal propagation |
| `sfsf-assistant-auth.properties` | `sfsf-assistant-auth` | Allows Joule to call the agent with `OAuth2JWTBearer` |

Fill in the `<placeholder>` values before importing into BTP Cockpit → Connectivity → Destinations.

## Customization

| File | What to change |
|------|---------------|
| `app/tools.py` | Add or replace OData tools for your use case |
| `app/agent.py` | Edit `SYSTEM_INSTRUCTION` to change agent behavior |
| `app/agent_card.py` | Update skills to match your tools |
| `joule-capability/scenarios/invoke_agent.yaml` | Update intent description for your domain |
| `manifest.yml` | Set your CF route, IAS config, and service bindings |
