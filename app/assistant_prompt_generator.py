"""
Assistant Prompt Generator - Genera system prompts desde archivos subidos.
Usa document loaders y LLM para extraer contenido y generar prompts.
"""

import os
from typing import List, Tuple
from langchain_google_genai import ChatGoogleGenerativeAI
from app.document_loaders import get_docs, FileType
from app.config import get_settings


def _file_path_to_url(file_path: str) -> str:
    """Convierte ruta storage/... a URL para document loaders (FileHandler espera /files/ en la ruta)."""
    settings = get_settings()
    base = settings.base_url.rstrip("/")
    if file_path.startswith("storage/"):
        rel = file_path.replace("storage/", "")
        return f"{base}/files/{rel}"
    return file_path


def _get_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".doc", ".docx"):
        return "docx"
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return "img"
    return "pdf"  # fallback


def generate_system_prompt_from_files(
    file_paths: List[Tuple[str, str]],  # [(path, filename), ...]
    user_hint: str = "",
) -> str:
    """
    Genera un system prompt a partir del contenido de archivos subidos.
    
    Args:
        file_paths: Lista de (ruta_completa, nombre_archivo)
        user_hint: Pista opcional del usuario sobre el propósito del asistente
        
    Returns:
        System prompt generado por el LLM
    """
    all_content: List[str] = []
    
    for file_path, filename in file_paths:
        if not os.path.exists(file_path):
            continue
        try:
            file_type = _get_file_type(filename)
            url = _file_path_to_url(file_path)
            
            if file_type == "img":
                content = get_docs(url, file_type, query="Extrae y resume todo el contenido relevante de esta imagen para definir un asistente de IA.", verbose=False)
                if isinstance(content, str):
                    all_content.append(f"[Imagen: {filename}]\n{content}")
                else:
                    all_content.append(f"[Imagen: {filename}]\n{str(content)[:8000]}")
            else:
                docs = get_docs(url, file_type, verbose=False)
                if docs and hasattr(docs[0], "page_content"):
                    text = "\n\n".join(d.page_content for d in docs)
                    all_content.append(f"[{filename}]\n{text[:8000]}")
                elif docs:
                    all_content.append(f"[{filename}]\n{str(docs)[:8000]}")
        except Exception as e:
            all_content.append(f"[Error procesando {filename}]: {str(e)}")
    
    if not all_content:
        return "Eres un asistente útil y profesional."
    
    combined = "\n\n---\n\n".join(all_content)[:15000]  # limit total
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7)
    
    prompt = f"""Analiza el siguiente contenido extraído de documentos e imágenes subidos por el usuario.
Tu tarea es generar un SYSTEM PROMPT profesional para un asistente de IA personalizado.

El system prompt debe:
1. Definir claramente el rol y expertise del asistente
2. Incluir instrucciones de comportamiento, tono y formato de respuesta
3. Incorporar el conocimiento relevante extraído de los documentos
4. Ser conciso pero completo (máximo 1500 palabras)
5. Estar en español

{f'Contexto adicional del usuario: {user_hint}' if user_hint else ''}

CONTENIDO DE LOS DOCUMENTOS:
{combined}

Genera ÚNICAMENTE el system prompt, sin explicaciones ni metadatos. El texto debe poder usarse directamente como system prompt."""

    response = llm.invoke(prompt)
    return (response.content or "Eres un asistente útil y profesional.").strip()
