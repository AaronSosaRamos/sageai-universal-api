import threading
from app.tools.tools import answer_question_from_file, search_scientific_resource, search_academic_papers, web_search
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from app.db.chat_management import ChatThreadRepository, ChatMessageCreate, ChatHistory
from app.memory_updater import MemoryUpdater
from app.db.memory_management import MemoryRepository

def get_supervisor_response(
    user_request: str,
    user_id: str,
    thread_id: str
):
    """
    Get the supervisor response for the user request.
    """

    print(f"Pregunta del usuario: {user_request}")

    model = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=1
    )

    previous_context = ChatHistory(ChatThreadRepository()).get_history_string(thread_id)

    print(f"Previous context: {previous_context}")

    # Obtener perfiles del usuario para personalización
    memory_repo = MemoryRepository()
    semantic_memory = memory_repo.get_semantic_memory(user_id)
    procedural_memory = memory_repo.get_procedural_memory(user_id)

    # Formatear perfiles para el prompt
    user_profile_section = ""
    if semantic_memory or procedural_memory:
        profile_parts = []
        
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
        
        user_profile_section = f"""
{'='*80}
INFORMACIÓN DE PERSONALIZACIÓN DEL USUARIO:
{''.join(profile_parts)}
IMPORTANTE: Usa esta información para personalizar tus respuestas según las preferencias, intereses, métodos y patrones del usuario. Adapta tu estilo de comunicación y contenido a sus necesidades específicas.
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
{user_request}

INSTRUCCIONES:
1. SI la pregunta se refiere a mensajes anteriores (ej: "qué dije antes", "recuerdas cuando...", "mencionaste..."), DEBES consultar el HISTORIAL DE CONVERSACIÓN ANTERIOR arriba y responder basándote en esa información.
2. Si hay información de PERSONALIZACIÓN DEL USUARIO disponible, úsala para adaptar tu respuesta a sus preferencias, intereses, métodos preferidos y estilo de trabajo. Personaliza el tono, nivel de detalle y enfoque según su perfil.
3. Si la pregunta requiere información de archivos, usa la herramienta answer_question_from_file.
4. Si la pregunta requiere búsqueda científica, usa search_scientific_resource.
5. Si el usuario solicita buscar artículos académicos, papers científicos, investigaciones recientes, literatura académica, o menciona fuentes como ArXiv, Scopus, ResearchGate, usa la herramienta search_academic_papers.
6. Si el usuario necesita información actualizada de internet, noticias recientes, datos en tiempo real, información que no está en los archivos proporcionados, o menciona "buscar en internet/web/google", usa la herramienta web_search.
7. Responde siempre en español usando formato Markdown cuando sea apropiado.
8. Sé natural, conversacional y útil, adaptándote al perfil del usuario cuando sea relevante.

RESPUESTA:"""
    )

    # Construir el mensaje completo con contexto
    if previous_context and previous_context.strip():
        full_user_message = f"""HISTORIAL DE CONVERSACIÓN ANTERIOR:
{previous_context}

PREGUNTA ACTUAL:
{user_request}

IMPORTANTE: Si la pregunta se refiere a mensajes anteriores, usa el historial de conversación anterior para responder."""
    else:
        full_user_message = user_request

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
    print(ai_response)

    # Store messages in chat thread
    chat_repo = ChatThreadRepository()
    
    # Store user message
    chat_repo.create_message(ChatMessageCreate(
        user_id=user_id,
        thread_id=thread_id,
        message=user_request,
        role="Human"
    ))
    
    # Store AI response
    chat_repo.create_message(ChatMessageCreate(
        user_id=user_id,
        thread_id=thread_id,
        message=ai_response,
        role="AI"
    ))

    # Contar mensajes del usuario en este thread (después de guardar el nuevo mensaje)
    thread_messages = chat_repo.get_thread_messages(thread_id, limit=200, ascending=True)
    user_messages_count = len([msg for msg in thread_messages if msg.role == "Human"])
    
    # Solo actualizar perfil cuando el usuario haya enviado exactamente 8 mensajes (o múltiplos de 8)
    # Esto significa que se ejecuta después del 8vo, 16vo, 24vo mensaje, etc.
    if user_messages_count > 0 and user_messages_count % 8 == 0:
        # Actualizar perfil del usuario de forma asíncrona (no bloquea la respuesta)
        def update_profile_async():
            """Actualiza el perfil del usuario en background después de 8 mensajes enviados."""
            try:
                import asyncio
                from app.memory_updater import MemoryUpdater
                updater = MemoryUpdater()
                # Ejecutar en un nuevo event loop para el thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # No necesitamos min_user_messages porque ya verificamos que tiene 8+
                loop.run_until_complete(updater.update_user_profile(user_id, thread_id, min_user_messages=8))
                loop.close()
            except Exception as e:
                print(f"Error en actualización asíncrona de perfil: {e}")

        # Iniciar actualización en thread separado (no bloquea)
        profile_update_thread = threading.Thread(target=update_profile_async, daemon=True)
        profile_update_thread.start()
        print(f"[Supervisor] Actualizando perfil después de {user_messages_count} mensajes del usuario en thread {thread_id}")
    else:
        print(f"[Supervisor] Usuario tiene {user_messages_count} mensajes en thread {thread_id}. Se ejecutará actualización después del 8vo mensaje.")

    return ai_response