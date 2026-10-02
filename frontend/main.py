"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over the A2A protocol, returning replies as
structured parts the chat UI knows how to show:

  * {"kind": "text", "text": ...}  -> a normal chat bubble
  * {"kind": "a2ui", "data": ...}  -> one A2UI message (beginRendering /
    surfaceUpdate); static/index.html renders these as a card.
"""

import json
import os
import uuid

import google.auth
import google.auth.transport.requests
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.protobuf.json_format import MessageToDict, ParseDict

RESOURCE = os.environ.get(
    "AGENT_ENGINE_RESOURCE_NAME",
    "projects/347608585474/locations/us-east1/reasoningEngines/7944139735960649728"
)
# The agent's app directory (matches agent_directory in agents-cli-manifest.yaml).
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
# Location is embedded in the resource name: projects/<p>/locations/<loc>/reasoningEngines/<id>.
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

# A2A endpoint for an Agent Runtime deployment, via the Agent Engine HTTP
# passthrough. The card lives at the well-known path under this base.
A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"

# The agent tags its A2UI data parts with this mime type.
_A2UI_MIME = "application/json+a2ui"

USER_ROLE = getattr(Role, "ROLE_USER", getattr(Role, "user", None))

# One set of ADC credentials, refreshed per request (access tokens expire ~1h).
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)


def _auth_headers() -> dict[str, str]:
    _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


app = FastAPI()


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Reuse ONE A2A context per user so the agent remembers the conversation.
_contexts: dict[str, str] = {}
# Cache the agent card after the first fetch.
_card: AgentCard | None = None


async def _get_card(client: httpx.AsyncClient) -> AgentCard:
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        card_data = resp.json()
        if isinstance(card_data, dict):
            if hasattr(AgentCard, "DESCRIPTOR"):
                _card = ParseDict(card_data, AgentCard(), ignore_unknown_fields=True)
            else:
                mapped = dict(card_data)
                if "supportedInterfaces" in mapped and "supported_interfaces" not in mapped:
                    mapped["supported_interfaces"] = mapped.pop("supportedInterfaces")
                if "defaultInputModes" in mapped and "default_input_modes" not in mapped:
                    mapped["default_input_modes"] = mapped.pop("defaultInputModes")
                if "defaultOutputModes" in mapped and "default_output_modes" not in mapped:
                    mapped["default_output_modes"] = mapped.pop("defaultOutputModes")
                if "additionalInterfaces" in mapped and "additional_interfaces" not in mapped:
                    mapped["additional_interfaces"] = mapped.pop("additionalInterfaces")
                if "preferredTransport" in mapped and "preferred_transport" not in mapped:
                    mapped["preferred_transport"] = mapped.pop("preferredTransport")
                if "protocolVersion" in mapped and "protocol_version" not in mapped:
                    mapped["protocol_version"] = mapped.pop("protocolVersion")
                if "documentationUrl" in mapped and "documentation_url" not in mapped:
                    mapped["documentation_url"] = mapped.pop("documentationUrl")
                if "securitySchemes" in mapped and "security_schemes" not in mapped:
                    mapped["security_schemes"] = mapped.pop("securitySchemes")
                if "securityRequirements" in mapped and "security_requirements" not in mapped:
                    mapped["security_requirements"] = mapped.pop("securityRequirements")
                if "iconUrl" in mapped and "icon_url" not in mapped:
                    mapped["icon_url"] = mapped.pop("iconUrl")
                if hasattr(AgentCard, "model_validate"):
                    _card = AgentCard.model_validate(mapped)
                else:
                    _card = AgentCard(**mapped)
        else:
            _card = card_data
    return _card


def _extract_parts(parts: list) -> list[dict]:
    out: list[dict] = []
    for p in parts:
        p_dict = (
            MessageToDict(p, preserving_proto_field_name=True)
            if hasattr(p, "DESCRIPTOR")
            else (p if isinstance(p, dict) else {})
        )
        text = p_dict.get("text")
        if text:
            out.append({"kind": "text", "text": text})
            continue

        data = p_dict.get("data")
        if isinstance(data, dict):
            meta = data.get("metadata", {})
            mime = meta.get("mimeType") or meta.get("mime_type")
            if mime == _A2UI_MIME:
                a2ui_payload = data.get("data", data)
                out.append({"kind": "a2ui", "data": a2ui_payload})
                continue
            elif (
                "surfaceUpdate" in data
                or "beginRendering" in data
                or "deleteSurface" in data
            ):
                out.append({"kind": "a2ui", "data": data})
                continue

        url = p_dict.get("url")
        if url:
            out.append({"kind": "text", "text": url})
    return out


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    user_id = body.get("user_id") or "web-user"
    parts: list[dict] = []

    async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
        card = await _get_card(client)
        factory = ClientFactory(
            ClientConfig(
                httpx_client=client,
            )
        )
        a2a_client = factory.create(card)

        user_part = Part(text=message)

        msg_kwargs = {
            "message_id": str(uuid.uuid4()),
            "role": USER_ROLE,
            "parts": [user_part],
        }
        ctx_id = _contexts.get(user_id)
        if ctx_id:
            msg_kwargs["context_id"] = ctx_id

        msg = Message(**msg_kwargs)
        send_req = SendMessageRequest(message=msg)

        async for event in a2a_client.send_message(send_req):
            if isinstance(event, tuple):
                task, update = event
                if task and getattr(task, "context_id", None):
                    _contexts[user_id] = task.context_id
                if update:
                    artifact = getattr(update, "artifact", None)
                    if artifact and hasattr(artifact, "parts"):
                        parts.extend(_extract_parts(artifact.parts))
            else:
                payload_type = (
                    event.WhichOneof("payload") if hasattr(event, "WhichOneof") else None
                )
                if payload_type == "task":
                    task = getattr(event, "task", None)
                    if task and getattr(task, "context_id", None):
                        _contexts[user_id] = task.context_id
                elif payload_type == "artifact_update":
                    artifact_update = getattr(event, "artifact_update", None)
                    if artifact_update and hasattr(artifact_update, "artifact"):
                        parts.extend(_extract_parts(artifact_update.artifact.parts))
                elif payload_type == "status_update":
                    status_update = getattr(event, "status_update", None)
                    if (
                        status_update
                        and hasattr(status_update, "context_id")
                        and status_update.context_id
                    ):
                        _contexts[user_id] = status_update.context_id

    if not parts:
        parts = [{"kind": "text", "text": "(The agent didn't return a reply.)"}]
    return JSONResponse({"parts": parts})


# Serve the chat UI (keep this mount last so /chat wins).
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
