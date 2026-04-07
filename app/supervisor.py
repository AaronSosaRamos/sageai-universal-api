import time

from app.tools.tools import (
    answer_question_from_file,
    search_scientific_resource,
    search_academic_papers,
    web_search,
    generate_practice_questions,
    create_learning_plan,
    create_study_notes,
    explain_concept_scaffolded,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.db.chat_management import ChatThreadRepository, ChatMessageCreate
from app.memory_updater import MemoryUpdater
from app.db.memory_management import MemoryRepository
from app.db.custom_space_management import CustomSpaceRepository
from app.db.user_management import UserRepository
from app.prompt_guard import (
    sanitize_user_input,
    sanitize_ai_response,
    get_defensive_supervisor_instructions,
    get_defensive_system_suffix,
    wrap_user_message_for_safety,
)
from app.document_loaders import (
    parse_files_block_from_message,
    build_file_context_for_llm,
)
from app.analytics_helpers import (
    extract_usage_from_gemini_langchain_messages,
    extract_usage_from_lc_invoke_response,
    record_llm_call,
)

SUPERVISOR_MODEL_NAME = "gemini-3.1-flash-lite-preview"

def get_supervisor_response(
    user_request: str,
    user_id: str,
    thread_id: str
):
    """
    Get the supervisor response for the user request.
    """
    user_request_sanitized, is_suspicious = sanitize_user_input(user_request)
    if not user_request_sanitized:
        return "Por favor envía un mensaje válido."

    full_user_message_for_llm = wrap_user_message_for_safety(user_request_sanitized) if is_suspicious else user_request_sanitized

    print(f"Pregunta del usuario: {user_request_sanitized[:200]}...")

    model = ChatGoogleGenerativeAI(
        model=SUPERVISOR_MODEL_NAME,
        temperature=0.75  # Más consistencia pedagógica; sigue siendo conversacional
    )

    chat_repo = ChatThreadRepository()
    history_messages = chat_repo.get_thread_messages(thread_id, limit=24, ascending=True)

    # Obtener información del usuario
    user_repo = UserRepository()
    user = user_repo.get_user(user_id)
    user_info = ""
    if user:
        user_info = f"""
INFORMACIÓN DEL USUARIO:
- Nombre: {user.nombre} {user.apellido}
- Email: {user.email}
"""

    # Obtener perfiles del usuario para personalización
    memory_repo = MemoryRepository()
    semantic_memory = memory_repo.get_semantic_memory(user_id)
    procedural_memory = memory_repo.get_procedural_memory(user_id)

    # Obtener espacio personalizado activo del usuario
    custom_space_repo = CustomSpaceRepository()
    custom_space = custom_space_repo.get_active_space(user_id)

    # Formatear perfiles para el prompt
    user_profile_section = ""
    profile_parts = []
    
    # Agregar información básica del usuario
    if user_info:
        profile_parts.append(user_info)
    
    # Agregar memorias semánticas y procedimentales si existen
    if semantic_memory or procedural_memory:
        if semantic_memory:
            profile_parts.append(f"""
PERFIL SEMÁNTICO DEL USUARIO ({semantic_memory.user_name}):
- Resumen: {semantic_memory.profile_summary}
- Conceptos Clave: {', '.join(semantic_memory.key_concepts[:10]) if semantic_memory.key_concepts else 'N/A'}
- Preferencias: {', '.join(semantic_memory.preferences[:10]) if semantic_memory.preferences else 'N/A'}
- Intereses: {', '.join(semantic_memory.interests[:10]) if semantic_memory.interests else 'N/A'}
- Dominios de Conocimiento: {', '.join(semantic_memory.knowledge_domains[:10]) if semantic_memory.knowledge_domains else 'N/A'}
""")
        
        if procedural_memory:
            profile_parts.append(f"""
PERFIL PROCEDIMENTAL DEL USUARIO ({procedural_memory.user_name}):
- Resumen: {procedural_memory.profile_summary}
- Métodos Preferidos: {', '.join(procedural_memory.preferred_methods[:10]) if procedural_memory.preferred_methods else 'N/A'}
- Procedimientos Comunes: {', '.join(procedural_memory.common_procedures[:10]) if procedural_memory.common_procedures else 'N/A'}
- Patrones de Flujo: {', '.join(procedural_memory.workflow_patterns[:10]) if procedural_memory.workflow_patterns else 'N/A'}
- Tips de Eficiencia: {', '.join(procedural_memory.efficiency_tips[:10]) if procedural_memory.efficiency_tips else 'N/A'}
""")
    
    # Agregar espacio personalizado si existe
    if custom_space:
        profile_parts.append(f"""
{'='*80}
ESPACIO PERSONALIZADO DEL USUARIO: {custom_space.title}
{'='*80}

MEMORIAS PERSONALIZADAS:
{custom_space.custom_memories if custom_space.custom_memories else 'No hay memorias personalizadas definidas.'}

INSTRUCCIONES DE COMPORTAMIENTO DEL AGENTE:
{custom_space.agent_instructions if custom_space.agent_instructions else 'No hay instrucciones específicas definidas.'}

IMPORTANTE: Estas instrucciones y memorias fueron definidas explícitamente por el usuario. DEBES seguir estas instrucciones y usar estas memorias para adaptar completamente tu comportamiento y respuestas según las preferencias del usuario.
{'='*80}
""")
    
    if profile_parts:
        user_profile_section = f"""
{'='*80}
INFORMACIÓN DE PERSONALIZACIÓN DEL USUARIO:
{''.join(profile_parts)}
IMPORTANTE: Usa toda esta información para personalizar completamente tus respuestas según las preferencias, intereses, métodos, patrones y instrucciones específicas del usuario. Adapta tu estilo de comunicación, nivel de detalle, enfoque y comportamiento según toda esta información.
{'='*80}
"""

    if user_profile_section:
        print(f"User profile section included: {semantic_memory.user_name if semantic_memory else 'N/A'}")

    supervisor = create_react_agent(
        model,
        tools=[
            search_scientific_resource,
            answer_question_from_file,
            search_academic_papers,
            web_search,
            generate_practice_questions,
            create_learning_plan,
            create_study_notes,
            explain_concept_scaffolded,
        ],
        prompt=f"""Eres un tutor profesional de educación personalizada. Tu rol es guiar el aprendizaje de forma conversacional, empática y adaptada a cada persona.

IDENTIDAD Y COMPORTAMIENTO:
- Tutor experto que usa el método socrático: haz preguntas que ayuden a reflexionar antes de dar respuestas directas.
- Andamiaje: adapta la profundidad al nivel del usuario (principiante, intermedio, experto).
- Multi-turn conversacional: mantén coherencia con todo lo dicho antes; usa referencias ("como vimos...", "retomando tu pregunta sobre...").
- No des respuestas triviales; profundiza cuando el tema lo amerite.
- Sé cálido pero profesional.

{user_profile_section}

HERRAMIENTAS DISPONIBLES:
- search_scientific_resource: Busca en recursos científicos cuando el usuario especifique archivos/URLs científicos.
- answer_question_from_file: Responde preguntas sobre archivos cuando el usuario proporcione archivos (PDF, docx, etc.).
- search_academic_papers: Busca artículos académicos en ArXiv, Scopus, ResearchGate cuando pidan papers, investigaciones o literatura científica.
- web_search: Busca en la web para información actualizada, noticias, documentación técnica o datos en tiempo real.
- generate_practice_questions: Genera preguntas de práctica/quiz cuando el usuario quiera practicar, evaluarse o reforzar un tema.
- create_learning_plan: Crea planes de aprendizaje con objetivos y hitos cuando pidan "plan de estudio", "qué debo aprender", "por dónde empiezo".
- create_study_notes: Genera notas de estudio cuando pidan resúmenes, apuntes, guía de repaso o consolidar lo discutido.
- explain_concept_scaffolded: Explica conceptos a nivel principiante/intermedio/experto cuando pidan "explica como si...", "no entiendo X", "qué es X en términos simples".

FORMATO DE ARCHIVOS (cuando el usuario los proporcione):
Files:
- http://example.com/files/path/file.pdf (File Type: pdf)
- http://example.com/files/path/image.jpg (File Type: img)
- http://example.com/files/path/doc.docx (File Type: docx)
- http://example.com/files/path/legacy.doc (File Type: doc)
- http://example.com/files/path/sheet.xlsx (File Type: xlsx)
- http://example.com/files/path/audio.mp3 (File Type: mp3)

TIPOS: pdf, doc, docx, xlsx, xls, img, mp3, url.

REGLAS:
1. Mantén el contexto conversacional: usa el historial para responder preguntas sobre mensajes previos.
2. Adapta tono y profundidad al perfil del usuario cuando exista información de personalización.
3. Prioriza herramientas cuando sean claramente útiles; no abuses si la respuesta es sencilla.
4. Responde siempre en español con Markdown cuando sea apropiado.
5. Sé natural y conversacional.

{get_defensive_supervisor_instructions()}"""
    )

    # Construir mensajes multi-turn: historial real + mensaje actual
    messages = []
    for m in history_messages:
        if m.role == "Human":
            messages.append(HumanMessage(content=m.message))
        else:
            messages.append(AIMessage(content=m.message))
    messages.append(HumanMessage(content=full_user_message_for_llm))

    user_input = {"messages": messages}
    response = supervisor.invoke(user_input)

    try:
        usage = extract_usage_from_gemini_langchain_messages(response.get("messages") or [])
        if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
            record_llm_call(
                model_name=SUPERVISOR_MODEL_NAME,
                user_id=user_id,
                thread_id=thread_id,
                assistant_id=None,
                provider="google",
                usage=usage,
                latency_ms=None,
                metadata={"flow": "supervisor_react_agent", "history_turns": len(history_messages)},
            )
    except Exception as ex:
        print(f"[analytics] supervisor LLM metrics: {ex}")

    # Extraer la última respuesta de texto del asistente (puede haber ToolMessages intermedios)
    ai_response = ""
    for msg in reversed(response["messages"]):
        if isinstance(msg, AIMessage) and msg.content:
            ai_response = msg.content if isinstance(msg.content, str) else str(msg.content)
            break
    if not ai_response and response["messages"]:
        last = response["messages"][-1]
        ai_response = getattr(last, "content", "") or ""
    ai_response = sanitize_ai_response(ai_response)
    print(ai_response)

    # Store messages in chat thread
    chat_repo = ChatThreadRepository()
    
    # Store user message (sanitized, not wrapped)
    chat_repo.create_message(ChatMessageCreate(
        user_id=user_id,
        thread_id=thread_id,
        message=user_request_sanitized,
        role="Human"
    ))
    
    # Store AI response
    chat_repo.create_message(ChatMessageCreate(
        user_id=user_id,
        thread_id=thread_id,
        message=ai_response,
        role="AI"
    ))

    # DESHABILITADO: Generación de memorias por chat
    # Contar mensajes del usuario en este thread (después de guardar el nuevo mensaje)
    # thread_messages = chat_repo.get_thread_messages(thread_id, limit=200, ascending=True)
    # user_messages_count = len([msg for msg in thread_messages if msg.role == "Human"])
    
    # Solo actualizar perfil cuando el usuario haya enviado exactamente 8 mensajes (o múltiplos de 8)
    # Esto significa que se ejecuta después del 8vo, 16vo, 24vo mensaje, etc.
    # if user_messages_count > 0 and user_messages_count % 8 == 0:
    #     # Actualizar perfil del usuario de forma asíncrona (no bloquea la respuesta)
    #     def update_profile_async():
    #         """Actualiza el perfil del usuario en background después de 8 mensajes enviados."""
    #         try:
    #             import asyncio
    #             from app.memory_updater import MemoryUpdater
    #             updater = MemoryUpdater()
    #             # Ejecutar en un nuevo event loop para el thread
    #             loop = asyncio.new_event_loop()
    #             asyncio.set_event_loop(loop)
    #             # No necesitamos min_user_messages porque ya verificamos que tiene 8+
    #             loop.run_until_complete(updater.update_user_profile(user_id, thread_id, min_user_messages=8))
    #             loop.close()
    #         except Exception as e:
    #             print(f"Error en actualización asíncrona de perfil: {e}")
    #
    #     # Iniciar actualización en thread separado (no bloquea)
    #     profile_update_thread = threading.Thread(target=update_profile_async, daemon=True)
    #     profile_update_thread.start()
    #     print(f"[Supervisor] Actualizando perfil después de {user_messages_count} mensajes del usuario en thread {thread_id}")
    # else:
    #     print(f"[Supervisor] Usuario tiene {user_messages_count} mensajes en thread {thread_id}. Se ejecutará actualización después del 8vo mensaje.")

    return ai_response


def get_assistant_chat_response(
    user_request: str,
    user_id: str,
    assistant_id: str,
    system_prompt: str
) -> str:
    """
    Chat con un asistente personalizado. Usa solo el system prompt del asistente,
    sin herramientas. Historial de chat por asistente (thread_id = assistant_{assistant_id}).
    Si el mensaje incluye el bloque Files: (PDF, Word, Excel), se extrae el texto con
    langchain-community y se inyecta como contexto antes de la pregunta.
    """
    user_request_sanitized, is_suspicious = sanitize_user_input(user_request)
    if not user_request_sanitized:
        return "Por favor envía un mensaje válido."

    user_text, file_pairs = parse_files_block_from_message(user_request_sanitized)
    file_context = ""
    if file_pairs:
        try:
            file_context = build_file_context_for_llm(file_pairs, verbose=False)
        except Exception as e:
            file_context = f"(No se pudo leer algún archivo adjunto: {e})"

    if file_context:
        combined_for_llm = (
            "El usuario adjuntó documentos. Usa SOLO el siguiente contexto extraído de esos archivos "
            "junto con su pregunta para responder.\n\n"
            f"{file_context}\n\n"
            f"---\nPregunta o instrucción del usuario:\n{user_text}"
        )
    else:
        combined_for_llm = user_text if user_text else user_request_sanitized

    llm_user_message = (
        wrap_user_message_for_safety(combined_for_llm) if is_suspicious else combined_for_llm
    )

    thread_id = f"assistant_{assistant_id}"
    chat_repo = ChatThreadRepository()
    history_messages = chat_repo.get_thread_messages(
        thread_id, limit=20, ascending=True, user_id=user_id
    )

    model = ChatGoogleGenerativeAI(
        model=SUPERVISOR_MODEL_NAME,
        temperature=0.7
    )

    defensive_suffix = get_defensive_system_suffix()
    full_system = f"""{system_prompt}

Responde siempre en español. Usa Markdown cuando sea apropiado.
Si se proporciona contexto de documentos adjuntos, básate en ese contenido para responder.{defensive_suffix}"""

    messages = [SystemMessage(content=full_system)]

    for m in history_messages:
        if m.role == "Human":
            messages.append(HumanMessage(content=m.message))
        else:
            messages.append(AIMessage(content=m.message))

    messages.append(HumanMessage(content=llm_user_message))

    t_llm = time.perf_counter()
    response = model.invoke(messages)
    llm_ms = int((time.perf_counter() - t_llm) * 1000)
    ai_response = response.content if hasattr(response, "content") else str(response)
    ai_response = sanitize_ai_response(ai_response)

    try:
        usage = extract_usage_from_lc_invoke_response(response)
        mn = str(usage.pop("model_name", None) or SUPERVISOR_MODEL_NAME)
        if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
            record_llm_call(
                model_name=mn,
                user_id=user_id,
                thread_id=thread_id,
                assistant_id=assistant_id,
                provider="google",
                usage=usage,
                latency_ms=llm_ms,
                metadata={
                    "flow": "assistant_chat",
                    "has_attached_file_context": bool(file_context),
                    "history_turns": len(history_messages),
                },
            )
    except Exception as ex:
        print(f"[analytics] assistant LLM metrics: {ex}")

    chat_repo.create_message(ChatMessageCreate(
        user_id=user_id,
        thread_id=thread_id,
        message=user_request_sanitized,
        role="Human"
    ))
    chat_repo.create_message(ChatMessageCreate(
        user_id=user_id,
        thread_id=thread_id,
        message=ai_response,
        role="AI"
    ))

    return ai_response