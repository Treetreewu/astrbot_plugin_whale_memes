"""Resolve persona using AstrBot's native session/conversation/default priority."""


async def persona(context, sp, event):
    umo = event.unified_msg_origin
    settings = await sp.get_async(scope="umo", scope_id=umo, key="session_service_config", default={})
    if settings.get("persona_id"):
        return settings["persona_id"]
    cid = await context.conversation_manager.get_curr_conversation_id(umo)
    conversation = await context.conversation_manager.get_conversation(umo, cid) if cid else None
    if conversation and conversation.persona_id:
        return conversation.persona_id
    default = await context.persona_manager.get_default_persona_v3(umo=umo)
    return default["name"] if default else "[%None]"
