import threading
from app.tools.tools import answer_question_from_file, search_scientific_resource, search_academic_papers, web_search
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from app.db.chat_management import ChatThreadRepository, ChatMessageCreate, ChatHistory
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
        model="gemini-2.5-flash",
        temperature=1
    )

    previous_context = ChatHistory(ChatThreadRepository()).get_history_string(thread_id)

    print(f"Previous context: {previous_context}")

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

    # Construir el prompt con el contexto claramente integrado
    context_section = ""
    if previous_context and previous_context.strip():
        context_section = f"""
HISTORIAL DE CONVERSACIÓN ANTERIOR:
{previous_context}

IMPORTANTE: Tienes acceso completo al historial de conversación anterior. Debes usar esta información para responder preguntas sobre mensajes previos, mantener el contexto de la conversación, y referirte a información mencionada anteriormente cuando sea relevante.
"""
    else:
        context_section = """
NOTA: Esta es una nueva conversación sin historial previo.
"""

    print(f"Context section: {context_section}")
    if user_profile_section:
        print(f"User profile section included: {semantic_memory.user_name if semantic_memory else 'N/A'}")

    supervisor = create_react_agent(
        model,
        tools=[
            search_scientific_resource,
            answer_question_from_file,
            search_academic_papers,
            web_search
        ],
        prompt=f"""Eres un asistente inteligente y útil que puede responder preguntas y ayudar con diversas tareas.

{user_profile_section}

{context_section}

HERRAMIENTAS DISPONIBLES:
- search_scientific_resource: Busca información en recursos científicos cuando el usuario especifique el recurso científico.
- answer_question_from_file: Responde preguntas sobre archivos cuando el usuario especifique el archivo.
- search_academic_papers: Busca y extrae los últimos artículos académicos de fuentes profesionales como ArXiv, Scopus, ResearchGate, etc. Usa esta herramienta cuando el usuario solicite buscar artículos académicos, papers científicos, investigaciones recientes, o literatura académica.
- web_search: Realiza búsquedas en la web para obtener información actualizada de internet, noticias, documentación técnica, o cualquier contenido disponible en la web. Usa esta herramienta cuando el usuario necesite información actualizada, noticias recientes, datos en tiempo real, o información que no está en los archivos proporcionados.

FORMATO DE ARCHIVOS (cuando el usuario los proporcione):
Files:
- http://example.com/files/path/file.pdf (File Type: pdf)
- http://example.com/files/path/image.jpg (File Type: img)
- http://example.com/files/path/doc.docx (File Type: docx)
- http://example.com/files/path/sheet.xlsx (File Type: xlsx)
- http://example.com/files/path/audio.mp3 (File Type: mp3)

TIPOS DE ARCHIVOS SOPORTADOS:
- pdf: Documentos PDF
- docx: Documentos de Word
- xlsx/xls: Hojas de cálculo Excel
- img: Imágenes (jpg, png, etc.)
- mp3: Archivos de audio
- url: URL de Sitios Web

PREGUNTA ACTUAL DEL USUARIO:
{full_user_message_for_llm}

INSTRUCCIONES:
1. SI la pregunta se refiere a mensajes anteriores (ej: "qué dije antes", "recuerdas cuando...", "mencionaste..."), DEBES consultar el HISTORIAL DE CONVERSACIÓN ANTERIOR arriba y responder basándote en esa información.
2. Si hay información de PERSONALIZACIÓN DEL USUARIO disponible, úsala para adaptar tu respuesta a sus preferencias, intereses, métodos preferidos y estilo de trabajo. Personaliza el tono, nivel de detalle y enfoque según su perfil.
3. Si la pregunta requiere información de archivos, usa la herramienta answer_question_from_file.
4. Si la pregunta requiere búsqueda científica, usa search_scientific_resource.
5. Si el usuario solicita buscar artículos académicos, papers científicos, investigaciones recientes, literatura académica, o menciona fuentes como ArXiv, Scopus, ResearchGate, usa la herramienta search_academic_papers.
6. Si el usuario necesita información actualizada de internet, noticias recientes, datos en tiempo real, información que no está en los archivos proporcionados, o menciona "buscar en internet/web/google", usa la herramienta web_search.
7. Responde siempre en español usando formato Markdown cuando sea apropiado.
8. Sé natural, conversacional y útil, adaptándote al perfil del usuario cuando sea relevante.

{get_defensive_supervisor_instructions()}

RESPUESTA:"""
    )

    # Construir el mensaje completo con contexto
    if previous_context and previous_context.strip():
        full_user_message = f"""HISTORIAL DE CONVERSACIÓN ANTERIOR:
{previous_context}

PREGUNTA ACTUAL:
{full_user_message_for_llm}

IMPORTANTE: Si la pregunta se refiere a mensajes anteriores, usa el historial de conversación anterior para responder."""
    else:
        full_user_message = full_user_message_for_llm

    user_input = {
        "messages": [
            {
                "role": "user",
                "content": full_user_message
            }
        ]
    }
    response = supervisor.invoke(user_input)
    ai_response = response["messages"][-1].content
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
    """
    user_request_sanitized, is_suspicious = sanitize_user_input(user_request)
    if not user_request_sanitized:
        return "Por favor envía un mensaje válido."

    llm_user_message = wrap_user_message_for_safety(user_request_sanitized) if is_suspicious else user_request_sanitized

    thread_id = f"assistant_{assistant_id}"
    chat_repo = ChatThreadRepository()
    history_messages = chat_repo.get_thread_messages(thread_id, limit=20, ascending=True)

    model = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.7
    )

    defensive_suffix = get_defensive_system_suffix()
    full_system = f"""{system_prompt}

Responde siempre en español. Usa Markdown cuando sea apropiado.{defensive_suffix}"""

    messages = [SystemMessage(content=full_system)]

    for m in history_messages:
        if m.role == "Human":
            messages.append(HumanMessage(content=m.message))
        else:
            messages.append(AIMessage(content=m.message))

    messages.append(HumanMessage(content=llm_user_message))

    response = model.invoke(messages)
    ai_response = response.content if hasattr(response, "content") else str(response)
    ai_response = sanitize_ai_response(ai_response)

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