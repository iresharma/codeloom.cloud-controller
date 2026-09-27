from __future__ import annotations

ENGINE_COMMANDS = frozenset(
    {
        "StartSession",
        "ListSessions",
        "SubmitUserMessage",
        "RequestSnapshot",
        "RequestOrchContext",
        "OpenFile",
        "CloseFile",
        "RequestGit",
        "UndoLastEdit",
        "AbortAgent",
        "AnswerPrompt",
        "Shutdown",
        "ReloadIntegrations",
        "SetMcpEnabled",
        "ActivateSkill",
        "CompleteMcpAuth",
        "CreatePath",
        "RenamePath",
        "DeletePath",
        "RequestContext",
        "RequestMemory",
        "RequestAgentTranscript",
    }
)

STREAM_LIMIT = 8 * 1024 * 1024


class ProtocolError(Exception):
    pass


def prepare_command(command: object, workspace: str) -> dict:
    if not isinstance(command, dict):
        raise ProtocolError("command must be an object")
    command_type = command.get("type")
    if not isinstance(command_type, str) or command_type not in ENGINE_COMMANDS:
        raise ProtocolError(f"unknown command: {command_type}")
    prepared = {key: value for key, value in command.items() if value is not None}
    prepared["type"] = command_type
    if command_type == "StartSession":
        prepared["workspace"] = workspace
    return prepared


def encode_command(command: object, workspace: str) -> bytes:
    import json

    prepared = prepare_command(command, workspace)
    return (json.dumps(prepared, separators=(",", ":")) + "\n").encode()
