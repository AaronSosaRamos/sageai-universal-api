"""
Memory Updater - Actualización asíncrona de perfiles de usuario.

Este módulo actualiza los perfiles semánticos y procedimentales del usuario
de forma asíncrona basándose en las conversaciones recientes.
"""

import asyncio
from typing import List, Dict, Any, Optional
from langchain_google_genai import ChatGoogleGenerativeAI
from app.db.chat_management import ChatThreadRepository, ChatHistory
from app.db.memory_management import MemoryRepository
from app.db.user_management import UserRepository
from app.toon_format import toon_to_dict


class MemoryUpdater:
    """
    Actualiza los perfiles de usuario de forma asíncrona.
    
    Extrae resúmenes estratégicos de los últimos threads y genera
    perfiles semánticos y procedimentales del usuario.
    """

    def __init__(self):
        self.chat_repo = ChatThreadRepository()
        self.memory_repo = MemoryRepository()
        self.user_repo = UserRepository()
        self.llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=1)

    async def update_user_profile(self, user_id: str, thread_id: str, min_user_messages: int = 8) -> Dict[str, Any]:
        """
        Actualiza el perfil del usuario de forma asíncrona basándose solo en el último thread.
        Solo se ejecuta si el usuario tiene al menos min_user_messages mensajes en ese thread.
        
        Args:
            user_id: ID del usuario
            thread_id: ID del thread más reciente a analizar
            min_user_messages: Número mínimo de mensajes del usuario requeridos (por defecto: 8)
            
        Returns:
            Dict con información sobre la actualización
        """
        try:
            # Obtener información del usuario
            user = self.user_repo.get_user(user_id)
            user_name = f"{user.nombre} {user.apellido}" if user else "Usuario"
            
            # Obtener mensajes del thread
            messages = self.chat_repo.get_thread_messages(thread_id, limit=200, ascending=True)
            
            if not messages:
                print(f"[Memory Updater] Usuario {user_name} ({user_id}): No se encontraron mensajes en el thread {thread_id}")
                return {"status": "skipped", "reason": "No messages found"}
            
            # Contar mensajes del usuario
            user_messages = [msg for msg in messages if msg.role == "Human"]
            user_message_count = len(user_messages)
            
            if user_message_count < min_user_messages:
                print(f"[Memory Updater] Usuario {user_name} ({user_id}): Solo {user_message_count} mensajes del usuario. Se requieren {min_user_messages} para actualizar perfil.")
                return {"status": "skipped", "reason": f"Insufficient messages: {user_message_count}/{min_user_messages}"}

            print(f"\n{'='*80}")
            print(f"[Memory Updater] Actualizando perfil para: {user_name} ({user_id})")
            print(f"[Memory Updater] Thread: {thread_id}")
            print(f"[Memory Updater] Mensajes del usuario: {user_message_count}")
            print(f"{'='*80}\n")

            # Extraer resumen estratégico solo del último thread
            summary = await self._extract_strategic_summary(user_id, thread_id, messages)
            if not summary:
                return {"status": "skipped", "reason": "Failed to extract summary"}
            
            summaries = [summary]
            print(f"[Memory Updater] Resumen extraído del thread {thread_id}")
            print(f"[Memory Updater] Resumen (primeros 500 chars): {summary.get('summary_text', '')[:500]}")
            
            # Generar perfiles
            semantic_profile = await self._generate_semantic_profile(user_id, user_name, summaries)
            procedural_profile = await self._generate_procedural_profile(user_id, user_name, summaries)
            
            # Imprimir perfiles generados
            self._print_profiles(user_name, semantic_profile, procedural_profile)
            
            # Guardar perfiles - filtrar solo los campos esperados
            allowed_semantic_fields = {
                'profile_summary', 'key_concepts', 'preferences', 
                'interests', 'knowledge_domains', 'tags'
            }
            filtered_semantic_profile = {
                k: v for k, v in semantic_profile.items() 
                if k in allowed_semantic_fields
            }
            
            semantic_memory = self.memory_repo.upsert_semantic_memory(
                user_id=user_id,
                user_name=user_name,
                **filtered_semantic_profile
            )
            
            # Filtrar solo los campos esperados para el perfil procedimental
            allowed_procedural_fields = {
                'profile_summary', 'preferred_methods', 'common_procedures',
                'workflow_patterns', 'efficiency_tips', 'tags'
            }
            filtered_procedural_profile = {
                k: v for k, v in procedural_profile.items() 
                if k in allowed_procedural_fields
            }
            
            procedural_memory = self.memory_repo.upsert_procedural_memory(
                user_id=user_id,
                user_name=user_name,
                **filtered_procedural_profile
            )

            print(f"\n{'='*80}")
            print(f"[Memory Updater] ✅ Perfiles actualizados exitosamente para {user_name}")
            print(f"{'='*80}\n")

            return {
                "status": "success",
                "thread_id": thread_id,
                "user_messages_count": user_message_count,
                "semantic_updated": True,
                "procedural_updated": True,
                "semantic_memory": semantic_memory,
                "procedural_memory": procedural_memory
            }
        except Exception as e:
            print(f"\n{'='*80}")
            print(f"[Memory Updater] ❌ Error actualizando perfil de usuario {user_id}: {e}")
            print(f"{'='*80}\n")
            import traceback
            traceback.print_exc()
            return {"status": "error", "error": str(e)}

    async def _extract_strategic_summary(
        self,
        user_id: str,
        thread_id: str,
        messages: List
    ) -> Optional[Dict[str, Any]]:
        """Extrae resumen estratégico del último thread."""
        try:
            # Obtener historial completo del thread
            chat_history = ChatHistory(self.chat_repo)
            history_text = chat_history.get_history_string(thread_id, limit=200, ascending=True)
            
            if not history_text or len(history_text.strip()) < 50:
                return None

            # Generar resumen estratégico usando LLM
            summary_prompt = f"""Analiza la siguiente conversación completa y extrae información estratégica sobre:
1. Preferencias del usuario
2. Intereses y temas de conversación
3. Patrones de comportamiento
4. Métodos y procedimientos mencionados

Conversación completa:
{history_text[:4000]}

Resumen estratégico (formato TOON):
preferences[2]{{value}}:
  preferencia1
  preferencia2
interests[2]{{value}}:
  interés1
  interés2
topics[2]{{value}}:
  tema1
  tema2
procedures[2]{{value}}:
  procedimiento1
  procedimiento2
key_concepts[2]{{value}}:
  concepto1
  concepto2

IMPORTANTE: Usa formato TOON, no JSON. El número entre corchetes debe ser el número real de elementos."""

            # Ejecutar LLM de forma síncrona en thread separado
            response = await asyncio.to_thread(
                lambda: self.llm.invoke(summary_prompt)
            )
            
            summary_text = response.content if hasattr(response, 'content') else str(response)
            
            # Parsear TOON a diccionario para almacenar
            try:
                summary_dict = toon_to_dict(summary_text)
                return {
                    "thread_id": thread_id,
                    "summary": summary_dict,
                    "summary_text": summary_text,
                    "last_message_at": messages[-1].created_at if messages else None
                }
            except Exception as e:
                print(f"Error parseando resumen TOON del thread {thread_id}: {e}")
                # Fallback: usar el texto como está
                return {
                    "thread_id": thread_id,
                    "summary": {"raw": summary_text},
                    "summary_text": summary_text,
                    "last_message_at": messages[-1].created_at if messages else None
                }
        except Exception as e:
            print(f"Error extrayendo resumen del thread {thread_id}: {e}")
            return None

    def _print_profiles(
        self,
        user_name: str,
        semantic_profile: Dict[str, Any],
        procedural_profile: Dict[str, Any]
    ):
        """Imprime los perfiles generados de forma formateada."""
        print(f"\n{'='*80}")
        print(f"📊 PERFIL SEMÁNTICO - {user_name}")
        print(f"{'='*80}")
        print(f"\n📝 Resumen:")
        print(f"   {semantic_profile.get('profile_summary', 'N/A')}")
        print(f"\n🔑 Conceptos Clave ({len(semantic_profile.get('key_concepts', []))}):")
        for concept in semantic_profile.get('key_concepts', [])[:10]:
            print(f"   • {concept}")
        print(f"\n❤️  Preferencias ({len(semantic_profile.get('preferences', []))}):")
        for pref in semantic_profile.get('preferences', [])[:10]:
            print(f"   • {pref}")
        print(f"\n🎯 Intereses ({len(semantic_profile.get('interests', []))}):")
        for interest in semantic_profile.get('interests', [])[:10]:
            print(f"   • {interest}")
        print(f"\n📚 Dominios de Conocimiento ({len(semantic_profile.get('knowledge_domains', []))}):")
        for domain in semantic_profile.get('knowledge_domains', [])[:10]:
            print(f"   • {domain}")
        if semantic_profile.get('tags'):
            print(f"\n🏷️  Tags: {', '.join(semantic_profile.get('tags', [])[:10])}")

        print(f"\n{'='*80}")
        print(f"⚙️  PERFIL PROCEDIMENTAL - {user_name}")
        print(f"{'='*80}")
        print(f"\n📝 Resumen:")
        print(f"   {procedural_profile.get('profile_summary', 'N/A')}")
        print(f"\n🔧 Métodos Preferidos ({len(procedural_profile.get('preferred_methods', []))}):")
        for method in procedural_profile.get('preferred_methods', [])[:10]:
            print(f"   • {method}")
        print(f"\n📋 Procedimientos Comunes ({len(procedural_profile.get('common_procedures', []))}):")
        for proc in procedural_profile.get('common_procedures', [])[:10]:
            print(f"   • {proc}")
        print(f"\n🔄 Patrones de Flujo ({len(procedural_profile.get('workflow_patterns', []))}):")
        for pattern in procedural_profile.get('workflow_patterns', [])[:10]:
            print(f"   • {pattern}")
        print(f"\n💡 Tips de Eficiencia ({len(procedural_profile.get('efficiency_tips', []))}):")
        for tip in procedural_profile.get('efficiency_tips', [])[:10]:
            print(f"   • {tip}")
        if procedural_profile.get('tags'):
            print(f"\n🏷️  Tags: {', '.join(procedural_profile.get('tags', [])[:10])}")
        print(f"\n{'='*80}\n")

    def _format_existing_semantic_profile(self, existing_memory) -> str:
        """Formatea un perfil semántico existente a formato TOON para incluir en el prompt."""
        if not existing_memory:
            return ""
        
        from app.toon_format import dict_to_toon
        
        profile_dict = {
            "profile_summary": existing_memory.profile_summary,
            "key_concepts": existing_memory.key_concepts or [],
            "preferences": existing_memory.preferences or [],
            "interests": existing_memory.interests or [],
            "knowledge_domains": existing_memory.knowledge_domains or [],
            "tags": existing_memory.tags or []
        }
        
        return dict_to_toon(profile_dict)

    async def _generate_semantic_profile(
        self,
        user_id: str,
        user_name: str,
        summaries: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Genera perfil semántico consolidado del usuario, reutilizando el perfil existente si existe."""
        # Obtener perfil existente
        existing_memory = self.memory_repo.get_semantic_memory(user_id)
        existing_profile_text = self._format_existing_semantic_profile(existing_memory) if existing_memory else ""
        
        if not summaries:
            # Si no hay resúmenes nuevos pero hay perfil existente, mantenerlo
            if existing_memory:
                return {
                    "profile_summary": existing_memory.profile_summary,
                    "key_concepts": existing_memory.key_concepts or [],
                    "preferences": existing_memory.preferences or [],
                    "interests": existing_memory.interests or [],
                    "knowledge_domains": existing_memory.knowledge_domains or [],
                    "tags": existing_memory.tags or []
                }
            # Perfil por defecto
            return {
                "profile_summary": f"{user_name} es un usuario nuevo sin historial suficiente para generar un perfil semántico detallado.",
                "key_concepts": [],
                "preferences": [],
                "interests": [],
                "knowledge_domains": [],
                "tags": []
            }

        # Usar el resumen del último thread
        summary_text = summaries[0].get("summary_text", str(summaries[0].get("summary", ""))) if summaries else ""
        thread_id_from_summary = summaries[0].get("thread_id", "unknown") if summaries else "unknown"
        
        print(f"[Memory Updater] Generando perfil semántico usando resumen del thread: {thread_id_from_summary}")
        print(f"[Memory Updater] Resumen a usar (primeros 300 chars): {summary_text[:300]}")
        
        # Construir prompt con perfil existente si está disponible
        existing_context = ""
        if existing_profile_text:
            existing_context = f"""
PERFIL SEMÁNTICO ACTUAL (preserva y mejora este perfil):
{existing_profile_text}

NUEVO RESUMEN DE CONVERSACIÓN (último thread: {thread_id_from_summary}):
"""
        else:
            existing_context = f"RESUMEN DE CONVERSACIÓN (último thread: {thread_id_from_summary}):\n"
        
        prompt = f"""Basándote en el perfil semántico actual (si existe) y el siguiente resumen de la última conversación del usuario {user_name} del thread {thread_id_from_summary}, genera un perfil semántico consolidado y actualizado que preserve la información existente y la mejore con los nuevos datos.

{existing_context}{summary_text[:3000]}

Genera un perfil semántico en formato TOON (ejemplo):
profile_summary: Resumen general del perfil semántico de {user_name} (2-3 oraciones). Incluye el nombre del usuario en el resumen.
key_concepts[3]{{value}}:
  concepto1
  concepto2
  concepto3
preferences[2]{{value}}:
  preferencia1
  preferencia2
interests[2]{{value}}:
  interés1
  interés2
knowledge_domains[2]{{value}}:
  dominio1
  dominio2
tags[2]{{value}}:
  tag1
  tag2

IMPORTANTE: 
- Usa formato TOON, NO JSON
- Si hay un PERFIL SEMÁNTICO ACTUAL, debes preservar y mejorar esa información, no reemplazarla completamente
- Consolida la información existente con los nuevos resúmenes, eliminando duplicados y agregando información nueva
- El profile_summary debe mencionar explícitamente el nombre del usuario ({user_name})
- El número entre corchetes debe ser el número real de elementos en cada array
- Solo retorna el formato TOON, sin markdown ni texto adicional"""

        try:
            response = await asyncio.to_thread(
                lambda: self.llm.invoke(prompt)
            )
            
            response_text = response.content if hasattr(response, 'content') else str(response)
            
            # Parsear formato TOON
            try:
                profile_data = toon_to_dict(response_text)
                
                # Asegurar que todos los campos requeridos existan
                if "profile_summary" not in profile_data:
                    profile_data["profile_summary"] = f"Perfil semántico generado automáticamente para {user_name}."
                if "key_concepts" not in profile_data:
                    profile_data["key_concepts"] = []
                if "preferences" not in profile_data:
                    profile_data["preferences"] = []
                if "interests" not in profile_data:
                    profile_data["interests"] = []
                if "knowledge_domains" not in profile_data:
                    profile_data["knowledge_domains"] = []
                if "tags" not in profile_data:
                    profile_data["tags"] = []
                
                # Convertir arrays si vienen como strings
                for key in ["key_concepts", "preferences", "interests", "knowledge_domains", "tags"]:
                    if key in profile_data and isinstance(profile_data[key], str):
                        profile_data[key] = [profile_data[key]]
                    elif key in profile_data and not isinstance(profile_data[key], list):
                        profile_data[key] = []
                
            except Exception as parse_error:
                print(f"Error parseando TOON del perfil semántico: {parse_error}")
                print(f"Response recibido: {response_text[:500]}")
                # Fallback si no se puede parsear
                profile_data = {
                    "profile_summary": f"Perfil semántico generado automáticamente para {user_name}.",
                    "key_concepts": [],
                    "preferences": [],
                    "interests": [],
                    "knowledge_domains": [],
                    "tags": []
                }

            return profile_data
        except Exception as e:
            print(f"Error generando perfil semántico: {e}")
            return {
                "profile_summary": f"Error generando perfil semántico para {user_name}.",
                "key_concepts": [],
                "preferences": [],
                "interests": [],
                "knowledge_domains": [],
                "tags": []
            }

    def _format_existing_procedural_profile(self, existing_memory) -> str:
        """Formatea un perfil procedimental existente a formato TOON para incluir en el prompt."""
        if not existing_memory:
            return ""
        
        from app.toon_format import dict_to_toon
        
        profile_dict = {
            "profile_summary": existing_memory.profile_summary,
            "preferred_methods": existing_memory.preferred_methods or [],
            "common_procedures": existing_memory.common_procedures or [],
            "workflow_patterns": existing_memory.workflow_patterns or [],
            "efficiency_tips": existing_memory.efficiency_tips or [],
            "tags": existing_memory.tags or []
        }
        
        return dict_to_toon(profile_dict)

    async def _generate_procedural_profile(
        self,
        user_id: str,
        user_name: str,
        summaries: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Genera perfil procedimental consolidado del usuario, reutilizando el perfil existente si existe."""
        # Obtener perfil existente
        existing_memory = self.memory_repo.get_procedural_memory(user_id)
        existing_profile_text = self._format_existing_procedural_profile(existing_memory) if existing_memory else ""
        
        if not summaries:
            # Si no hay resúmenes nuevos pero hay perfil existente, mantenerlo
            if existing_memory:
                return {
                    "profile_summary": existing_memory.profile_summary,
                    "preferred_methods": existing_memory.preferred_methods or [],
                    "common_procedures": existing_memory.common_procedures or [],
                    "workflow_patterns": existing_memory.workflow_patterns or [],
                    "efficiency_tips": existing_memory.efficiency_tips or [],
                    "tags": existing_memory.tags or []
                }
            # Perfil por defecto
            return {
                "profile_summary": f"{user_name} es un usuario nuevo sin historial suficiente para generar un perfil procedimental detallado.",
                "preferred_methods": [],
                "common_procedures": [],
                "workflow_patterns": [],
                "efficiency_tips": [],
                "tags": []
            }

        # Usar el resumen del último thread
        summary_text = summaries[0].get("summary_text", str(summaries[0].get("summary", ""))) if summaries else ""
        thread_id_from_summary = summaries[0].get("thread_id", "unknown") if summaries else "unknown"
        
        print(f"[Memory Updater] Generando perfil procedimental usando resumen del thread: {thread_id_from_summary}")
        
        # Construir prompt con perfil existente si está disponible
        existing_context = ""
        if existing_profile_text:
            existing_context = f"""
PERFIL PROCEDIMENTAL ACTUAL (preserva y mejora este perfil):
{existing_profile_text}

NUEVO RESUMEN DE CONVERSACIÓN (último thread: {thread_id_from_summary}):
"""
        else:
            existing_context = f"RESUMEN DE CONVERSACIÓN (último thread: {thread_id_from_summary}):\n"
        
        prompt = f"""Basándote en el perfil procedimental actual (si existe) y el siguiente resumen de la última conversación del usuario {user_name} del thread {thread_id_from_summary}, genera un perfil procedimental consolidado y actualizado que preserve la información existente y la mejore con los nuevos datos.

{existing_context}{summary_text[:3000]}

Genera un perfil procedimental en formato TOON (ejemplo):
profile_summary: Resumen general del perfil procedimental de {user_name} (2-3 oraciones). Incluye el nombre del usuario en el resumen.
preferred_methods[2]{{value}}:
  método1
  método2
common_procedures[2]{{value}}:
  procedimiento1
  procedimiento2
workflow_patterns[2]{{value}}:
  patrón1
  patrón2
efficiency_tips[2]{{value}}:
  tip1
  tip2
tags[2]{{value}}:
  tag1
  tag2

IMPORTANTE: 
- Usa formato TOON, NO JSON
- Si hay un PERFIL PROCEDIMENTAL ACTUAL, debes preservar y mejorar esa información, no reemplazarla completamente
- Consolida la información existente con los nuevos resúmenes, eliminando duplicados y agregando información nueva
- El profile_summary debe mencionar explícitamente el nombre del usuario ({user_name})
- El número entre corchetes debe ser el número real de elementos en cada array
- Solo retorna el formato TOON, sin markdown ni texto adicional"""

        try:
            response = await asyncio.to_thread(
                lambda: self.llm.invoke(prompt)
            )
            
            response_text = response.content if hasattr(response, 'content') else str(response)
            
            # Parsear formato TOON
            try:
                profile_data = toon_to_dict(response_text)
                
                # Asegurar que todos los campos requeridos existan
                if "profile_summary" not in profile_data:
                    profile_data["profile_summary"] = f"Perfil procedimental generado automáticamente para {user_name}."
                if "preferred_methods" not in profile_data:
                    profile_data["preferred_methods"] = []
                if "common_procedures" not in profile_data:
                    profile_data["common_procedures"] = []
                if "workflow_patterns" not in profile_data:
                    profile_data["workflow_patterns"] = []
                if "efficiency_tips" not in profile_data:
                    profile_data["efficiency_tips"] = []
                if "tags" not in profile_data:
                    profile_data["tags"] = []
                
                # Convertir arrays si vienen como strings
                for key in ["preferred_methods", "common_procedures", "workflow_patterns", "efficiency_tips", "tags"]:
                    if key in profile_data and isinstance(profile_data[key], str):
                        profile_data[key] = [profile_data[key]]
                    elif key in profile_data and not isinstance(profile_data[key], list):
                        profile_data[key] = []
                
            except Exception as parse_error:
                print(f"Error parseando TOON del perfil procedimental: {parse_error}")
                print(f"Response recibido: {response_text[:500]}")
                # Fallback si no se puede parsear
                profile_data = {
                    "profile_summary": f"Perfil procedimental generado automáticamente para {user_name}.",
                    "preferred_methods": [],
                    "common_procedures": [],
                    "workflow_patterns": [],
                    "efficiency_tips": [],
                    "tags": []
                }

            return profile_data
        except Exception as e:
            print(f"Error generando perfil procedimental: {e}")
            return {
                "profile_summary": f"Error generando perfil procedimental para {user_name}.",
                "preferred_methods": [],
                "common_procedures": [],
                "workflow_patterns": [],
                "efficiency_tips": [],
                "tags": []
            }


# Función helper para ejecutar actualización asíncrona
def update_user_profile_async(user_id: str, thread_id: str, min_user_messages: int = 8):
    """
    Ejecuta la actualización del perfil del usuario de forma asíncrona.
    Esta función no bloquea y se ejecuta en segundo plano.
    Solo se ejecuta si el usuario tiene al menos min_user_messages mensajes en el thread.
    
    Args:
        user_id: ID del usuario
        thread_id: ID del thread más reciente
        min_user_messages: Número mínimo de mensajes del usuario requeridos (por defecto: 8)
    """
    async def _update():
        updater = MemoryUpdater()
        result = await updater.update_user_profile(user_id, thread_id, min_user_messages)
        print(f"Perfil actualizado para usuario {user_id}: {result}")
    
    # Ejecutar en background sin bloquear
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Si ya hay un loop corriendo, crear una tarea
            asyncio.create_task(_update())
        else:
            # Si no hay loop, ejecutar directamente
            asyncio.run(_update())
    except RuntimeError:
        # Si no hay event loop, crear uno nuevo
        asyncio.run(_update())

