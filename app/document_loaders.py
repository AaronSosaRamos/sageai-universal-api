
import os
import tempfile
import uuid
from langchain_community.document_loaders import AsyncChromiumLoader
from langchain_community.document_transformers import Html2TextTransformer

from langchain_text_splitters import RecursiveCharacterTextSplitter
import aiohttp
import asyncio
from enum import Enum

from langchain_community.document_loaders import (
    PyPDFLoader, 
    Docx2txtLoader, 
    UnstructuredExcelLoader
)

from dotenv import load_dotenv, find_dotenv

import requests

load_dotenv(find_dotenv())

# Configurar timeout para aiohttp
TIMEOUT = aiohttp.ClientTimeout(total=30)  # 30 segundos en total

class FileType(Enum):
    PDF = 'pdf'
    DOCX = "docx"
    XLS = "xls"
    XLSX = "xlsx"
    IMG = 'img'
    MP3 = 'mp3'
    URL = 'url'

splitter = RecursiveCharacterTextSplitter(
    chunk_size = 1000,
    chunk_overlap = 100
)


def get_docs(file_url: str, file_type: str, query: str = '', verbose=True):
    file_type = file_type.lower()

    try:
        docs = []

        file_loader = file_loader_map[FileType(file_type)]

        if file_type.lower() == 'img':
            docs = file_loader(file_url, query, verbose)
        else:
            docs = file_loader(file_url, verbose)

        return docs

    except KeyError as e:
        print(f"Unsupported file type: {file_type}")
        raise e
    
    except Exception as e:
        print(f"Failed to load the document: {e}")
        raise e

def load_url_documents(url: str, verbose=False):
    try:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size = 1000,
            chunk_overlap = 100
        )

        loader = AsyncChromiumLoader([url], user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        docs = loader.load()

        html2text = Html2TextTransformer()
        docs_transformed = html2text.transform_documents(docs)

        if docs:
            split_docs = splitter.split_documents(docs_transformed)
            return split_docs
    except Exception as e:
        print(e)
        raise e


class FileHandler:
    def __init__(self, file_loader, file_extension):
        self.file_loader = file_loader
        self.file_extension = file_extension

    def load(self, url):
        try:
            print(f"\n[FileHandler] Procesando archivo desde: {url}")
            
            # Extraer los UUIDs y nombre del archivo de la URL
            parts = url.split('/files/')[1].split('/')
            if len(parts) != 3:
                raise ValueError(f"URL inválida: {url}")
                
            session_uuid, inner_uuid, filename = parts
            file_path = f"storage/{session_uuid}/{inner_uuid}/{filename}"
            
            print(f"[FileHandler] Ruta del archivo: {file_path}")
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Archivo no encontrado: {file_path}")
                
            print(f"[FileHandler] Archivo encontrado, tamaño: {os.path.getsize(file_path)} bytes")
        except Exception as e:
            print(f"An error occurred while downloading or saving the file: {e}")
            raise e

        # Use the file_loader to load the documents
        try:
            print(f"\n[FileHandler] Iniciando carga del documento con {self.file_loader.__name__}")
            loader = self.file_loader(file_path)
            documents = loader.load()
            print(f"[FileHandler] Documento cargado exitosamente. Páginas/Documentos: {len(documents)}")
            return documents
        except Exception as e:
            print(f"[FileHandler] Error al cargar documento: {str(e)}")
            print(f"[FileHandler] Tipo de error: {type(e).__name__}")
            print("[FileHandler] File content might be private or unavailable or the URL is incorrect.")
            raise e

        return documents

def load_pdf_documents(pdf_url: str, verbose=False):
    pdf_loader = FileHandler(PyPDFLoader, "pdf")
    docs = pdf_loader.load(pdf_url)

    if docs:
        split_docs = splitter.split_documents(docs)

        if verbose:
            print(f"Found PDF file")
            print(f"Splitting documents into {len(split_docs)} chunks")

        return split_docs

def load_docx_documents(docx_url: str, verbose=False):
    docx_handler = FileHandler(Docx2txtLoader, 'docx')
    docs = docx_handler.load(docx_url)
    if docs: 

        split_docs = splitter.split_documents(docs)

        if verbose:
            print(f"Found DOCX file")
            print(f"Splitting documents into {len(split_docs)} chunks")

        return split_docs

def load_xls_documents(xls_url: str, verbose=False):
    xls_handler = FileHandler(UnstructuredExcelLoader, 'xls')
    docs = xls_handler.load(xls_url)
    if docs: 

        split_docs = splitter.split_documents(docs)

        if verbose:
            print(f"Found XLS file")
            print(f"Splitting documents into {len(split_docs)} chunks")

        return split_docs

def load_xlsx_documents(xlsx_url: str, verbose=False):
    xlsx_handler = FileHandler(UnstructuredExcelLoader, 'xlsx')
    docs = xlsx_handler.load(xlsx_url)
    if docs: 

        split_docs = splitter.split_documents(docs)

        if verbose:
            print(f"Found XLSX file")
            print(f"Splitting documents into {len(split_docs)} chunks")

        return split_docs

def download_image(url: str, temp_dir: str = None) -> str:
    """
    Descarga una imagen desde una URL o lee directamente del sistema de archivos si es local.
    Retorna la ruta del archivo temporal (o local si es del storage).
    """
    import requests
    import tempfile
    import os
    from urllib.parse import urlparse

    try:
        # Si es una URL local del storage, leer directamente del sistema de archivos
        if '/files/' in url:
            parts = url.split('/files/')[1].split('/')
            if len(parts) == 3:
                session_uuid, inner_uuid, filename = parts
                file_path = f"storage/{session_uuid}/{inner_uuid}/{filename}"
                if os.path.exists(file_path):
                    # Retornar la ruta directamente sin copiar
                    return file_path
        
        # Si no es local, descargar desde HTTP
        # Crear directorio temporal si no se proporciona uno
        if not temp_dir:
            temp_dir = tempfile.gettempdir()

        # Obtener extensión del archivo desde la URL o usar .jpg por defecto
        parsed_url = urlparse(url)
        path = parsed_url.path
        ext = os.path.splitext(path)[1].lower()
        if not ext or ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            ext = '.jpg'

        # Crear nombre de archivo temporal único
        temp_file = tempfile.NamedTemporaryFile(suffix=ext, dir=temp_dir, delete=False)
        temp_path = temp_file.name

        # Descargar imagen
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        # Guardar imagen
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return temp_path

    except Exception as e:
        print(f"Error downloading image: {e}")
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise

def encode_image(image_path: str) -> str:
    """
    Codifica una imagen en base64.
    """
    import base64
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def load_img_documents(img_url: str, query: str = '', verbose=False):
    """
    Carga y procesa una imagen usando Google Gemini API.
    La imagen se descarga a un directorio temporal y se codifica en base64.
    """
    import os

    temp_path = None
    try:
        if verbose:
            print(f"Query: {query}")
            print(f"Image URL: {img_url}")

        # Descargar imagen a directorio temporal
        temp_path = download_image(img_url)
        if verbose:
            print(f"Image downloaded to: {temp_path}")

        # Usar Gemini para análisis de imágenes
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage
        
        llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview")
        
        # Codificar imagen en base64
        base64_image = encode_image(temp_path)
        
        # Crear mensaje con formato correcto para Gemini
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": query or "¿Qué hay en esta imagen?",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                }
            ]
        )
        
        response = llm.invoke([message])
        
        if verbose:
            print(f"Response: {response.content}")

        return response.content

    except Exception as e:
        print(f"Failed to process image: {e}")
        raise

    finally:
        # Limpiar archivo temporal solo si es un archivo temporal (no del storage local)
        if temp_path and os.path.exists(temp_path):
            # No eliminar si es del storage local (contiene "storage/")
            if "storage/" not in temp_path:
                try:
                    os.unlink(temp_path)
                    if verbose:
                        print(f"Temporary file removed: {temp_path}")
                except Exception as e:
                    print(f"Warning: Could not remove temporary file {temp_path}: {e}")

def download_mp3_file(url: str, filename: str = None) -> str:
    """
    Downloads a .mp3 file from a given URL or reads directly from filesystem if local.
    Saves it in a temp directory if downloaded from HTTP.

    Args:
        url (str): The URL of the .mp3 file.
        filename (str, optional): Desired file name without extension. If None, a temp name will be generated.

    Returns:
        str: Full path to the downloaded .mp3 file (or local path if from storage).
    """
    if not url.lower().endswith('.mp3'):
        raise ValueError("The URL does not point to a .mp3 file.")

    try:
        # Si es una URL local del storage, leer directamente del sistema de archivos
        if '/files/' in url:
            parts = url.split('/files/')[1].split('/')
            if len(parts) == 3:
                session_uuid, inner_uuid, filename_part = parts
                file_path = f"storage/{session_uuid}/{inner_uuid}/{filename_part}"
                if os.path.exists(file_path):
                    # Retornar la ruta directamente sin copiar
                    return file_path
        
        # Si no es local, descargar desde HTTP
        response = requests.get(url, stream=True)
        response.raise_for_status()

        temp_dir = tempfile.gettempdir()
        file_name = f"{filename or next(tempfile._get_candidate_names())}.mp3"
        file_path = os.path.join(temp_dir, file_name)

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return file_path

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to download the file: {e}")


def load_mp3_documents(mp3_url: str, verbose=False):
    """
    Carga y transcribe un archivo de audio usando Google Gemini API.
    El audio se descarga a un directorio temporal si es necesario o se lee directamente del storage.
    """
    import os
    
    mp3_path = None
    try:
        if verbose:
            print(f"Audio URL: {mp3_url}")

        # Descargar o obtener ruta del archivo de audio
        mp3_path = download_mp3_file(mp3_url)
        if verbose:
            print(f"Audio file path: {mp3_path}")

        # Leer el archivo de audio como bytes
        with open(mp3_path, "rb") as audio_file:
            audio_content = audio_file.read()
        
        # Usar Gemini para transcripción de audio
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage
        
        llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview")
        
        # Crear mensaje con formato correcto para Gemini
        # Gemini acepta audio como bytes directamente o como base64
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": "Transcribe el siguiente audio de forma completa y detallada:",
                },
                {
                    "type": "audio",
                    "audio": audio_content,
                }
            ]
        )
        
        response = llm.invoke([message])
        
        transcript_text = response.content
        
        if verbose:
            print(f"Transcript: {transcript_text}")

        return transcript_text

    except Exception as e:
        print(f"Failed to process audio: {e}")
        raise

    finally:
        # Limpiar archivo temporal solo si es un archivo temporal (no del storage local)
        if mp3_path and os.path.exists(mp3_path):
            # No eliminar si es del storage local (contiene "storage/")
            if "storage/" not in mp3_path:
                try:
                    os.unlink(mp3_path)
                    if verbose:
                        print(f"Temporary file removed: {mp3_path}")
                except Exception as e:
                    print(f"Warning: Could not remove temporary file {mp3_path}: {e}")


file_loader_map = {
    FileType.PDF: load_pdf_documents,
    FileType.DOCX: load_docx_documents,
    FileType.XLS: load_xls_documents,
    FileType.XLSX: load_xlsx_documents,
    FileType.IMG: load_img_documents,
    FileType.MP3: load_mp3_documents,
    FileType.URL: load_url_documents
}