from typing import Annotated, Literal, Optional

from app.document_loaders import load_pdf_documents, load_url_documents
from langchain_google_genai import ChatGoogleGenerativeAI

from dotenv import load_dotenv, find_dotenv
import os

from app.document_loaders import get_docs
from typing import List
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime
import urllib.parse
import json

load_dotenv(find_dotenv(), override=True)

def search_scientific_resource(
    user_full_requirement: Annotated[str, "The full requirement of the user"],
    file_urls: Annotated[Optional[List[str]], "The urls of the scientific resources"] = None,
    file_types: Annotated[Optional[List[Literal[
        "pdf",
        "img",
        "mp3",
        "url",
        "docx",
        "xls",
        "xlsx"
    ]]], "The types of the files"] = None
):
    """
    Search scientific resource for the user's full requirement.
    """
    try:
        print(f"Buscando información en el recurso científico: {file_urls if file_urls else 'Ninguno'}")
        print(f"Pregunta del usuario: {user_full_requirement}")
        additional_context = ""
        final_response = ""

        #if file_url and file_type == "pdf":
        # docs = load_url_documents(
        #     url=file_url
        # )

        if file_urls and file_types:

            print(f"Buscando información en los recursos científicos: {file_urls}")
            print(f"Tipos de archivos: {file_types}")

            for file_url, file_type in zip(file_urls, file_types):
                docs = get_docs(file_url, file_type, query=user_full_requirement)

                if file_type == "img" or file_type == "mp3":
                    additional_context += docs + "\n"
                else:
                    for doc in docs:
                        additional_context += doc.page_content + "\n"
            
            print(f"Contexto adicional: {additional_context}")

        #elif file_url and file_type == "img":

            # client = OpenAI()

            # response = client.responses.create(
            #     model="gpt-4o",
            #     input=[{
            #         "role": "user",
            #         "content": [
            #             {"type": "input_text", "text": f"Responde la pregunta del usuario: {user_full_requirement}"},
            #             {
            #                 "type": "input_image",
            #                 "image_url": file_url,
            #             },
            #         ],
            #     }],
            # )

            # final_response = response.output_text

            # print(f"Respuesta: {final_response}")

            # return final_response

        prompt = f"""
        Eres un asistente útil que puede buscar recursos científicos para satisfacer completamente el requerimiento del usuario.

        Pregunta del usuario: {user_full_requirement}

        Contexto adicional: {additional_context}

        Si se tiene contexto adicional, responde la pregunta del usuario con base en el contexto proporcionado. Haz referencias directas a las fuentes de información.

        Responde la pregunta del usuario con base en el contexto proporcionado. La respuesta debe ser en español con Markdown Format. No uses hipervínculos. Citas APA 7 necesario

        Si no hay contexto adicional, no fuerces las citas académicas.
        
        Respuesta:
        """

        model = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=1
        )

        final_response = model.invoke(prompt).content

        print(f"Respuesta: {final_response}")

        return final_response

    except Exception as e:
        print(e)
        raise e
    
def answer_question_from_file(
    file_urls: Annotated[List[str], "The url of the file"],
    file_types: Annotated[List[Literal[
        "pdf",
        "img",
        "mp3",
        "url",
        "docx",
        "xls",
        "xlsx"
    ]], "The type of the file"],
    question: Annotated[str, "The question to answer"]
):
    """
    Answer a question from a file.
    """
    try:
        print(f"Buscando información en el archivo: {file_urls}")
        print(f"Tipo de archivo: {file_types}")
        print(f"Pregunta del usuario: {question}")
        
        model = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=1
        )

        context = ""
        response = ""

        if file_urls and file_types:

            print(f"Buscando información en los recursos científicos: {file_urls}")
            print(f"Tipos de archivos: {file_types}")

            for file_url, file_type in zip(file_urls, file_types):
                docs = get_docs(file_url, file_type, query=question)

                if file_type == "img" or file_type == "mp3":
                    context += docs + "\n"
                else:
                    for doc in docs:
                        context += doc.page_content + "\n"

        #if file_type == "pdf":

            # docs = load_pdf_documents(
            #     pdf_url=file_url
            # )

            # for doc in docs:
            #     context += doc.page_content + "\n"

        prompt = f"""
        Eres un asistente especializado en análisis y extracción de información detallada desde archivos.  
        Tu objetivo es elaborar una respuesta **extensa, exhaustiva y específica**, incorporando la **máxima cantidad posible de datos relevantes y precisos** provenientes del contexto proporcionado.  

        **Pregunta del usuario:**  
        {question}  

        **Contexto disponible (fuente de datos):**  
        {context}  

        **Instrucciones para la respuesta:**  
        1. La respuesta debe estar **en español** y en formato **Markdown**.  
        2. Utiliza la **mayor cantidad de datos concretos** que existan en el contexto, incluyendo cifras, fechas, nombres, ubicaciones, términos técnicos, descripciones y ejemplos exactos.  
        3. **No omitas información relevante** que se encuentre en el contexto, aunque no haya sido explícitamente solicitada por el usuario, si ayuda a dar una respuesta más completa.  
        4. **No inventes información**: todo lo que digas debe estar respaldado únicamente por el contexto dado.  
        5. **No uses hipervínculos**. Cuando hagas referencias, menciona de forma textual los datos tal como aparecen en las fuentes.  
        6. Si el contexto contiene varias secciones, **integra y conecta la información** para que la respuesta sea coherente y no fragmentada.  
        7. Estructura la respuesta con **encabezados, listas y tablas** cuando sea posible, para mejorar la legibilidad.  
        8. Si algún dato clave no aparece en el contexto, indícalo explícitamente como **"No especificado en la fuente"**.

        **Respuesta:**
        """

        response = model.invoke(prompt).content

        print(f"Respuesta: {response}")

        return response
        
        #elif file_type == "img":

            # client = OpenAI()

            # response = client.responses.create(
            #     model="gpt-4o",
            #     input=[{
            #         "role": "user",
            #         "content": [
            #             {"type": "input_text", "text": f"Answer this question: {question}"},
            #             {
            #                 "type": "input_image",
            #                 "image_url": file_url,
            #             },
            #         ],
            #     }],
            # )
            
            # final_response = response.output_text

            # print(f"Respuesta: {final_response}")

            # return final_response

    except Exception as e:
        print(e)
        raise e


def search_academic_papers(
    query: Annotated[str, "Término de búsqueda para encontrar artículos académicos"],
    source: Annotated[Literal["arxiv", "researchgate", "scopus", "all"], "Fuente académica: arxiv, researchgate, scopus, o all para buscar en todas"] = "all",
    max_results: Annotated[int, "Número máximo de resultados a retornar (máximo 50)"] = 10,
    sort_by: Annotated[Literal["relevance", "date", "citations"], "Ordenar resultados por: relevance, date, o citations"] = "relevance"
):
    """
    Busca y extrae los últimos artículos académicos de fuentes profesionales como ArXiv, Scopus, ResearchGate, etc.
    
    Esta herramienta permite buscar artículos académicos recientes de múltiples fuentes profesionales.
    Retorna información detallada incluyendo título, autores, resumen, fecha de publicación, y enlaces.
    """
    try:
        print(f"Buscando artículos académicos: '{query}' en fuente: {source}")
        
        # Limitar max_results
        max_results = min(max_results, 50)
        
        results = []
        
        # Buscar en ArXiv
        if source in ["arxiv", "all"]:
            try:
                arxiv_results = _search_arxiv(query, max_results, sort_by)
                results.extend(arxiv_results)
                print(f"Encontrados {len(arxiv_results)} artículos en ArXiv")
            except Exception as e:
                print(f"Error buscando en ArXiv: {e}")
        
        # Buscar en ResearchGate
        if source in ["researchgate", "all"]:
            try:
                rg_results = _search_researchgate(query, max_results, sort_by)
                results.extend(rg_results)
                print(f"Encontrados {len(rg_results)} artículos en ResearchGate")
            except Exception as e:
                print(f"Error buscando en ResearchGate: {e}")
        
        # Buscar en Scopus (búsqueda básica)
        if source in ["scopus", "all"]:
            try:
                scopus_results = _search_scopus(query, max_results, sort_by)
                results.extend(scopus_results)
                print(f"Encontrados {len(scopus_results)} artículos en Scopus")
            except Exception as e:
                print(f"Error buscando en Scopus: {e}")
        
        # Ordenar resultados según sort_by
        if sort_by == "date":
            results.sort(key=lambda x: x.get("published_date", ""), reverse=True)
        elif sort_by == "citations":
            results.sort(key=lambda x: x.get("citations", 0), reverse=True)
        # relevance ya está ordenado por relevancia de cada fuente
        
        # Limitar resultados finales
        results = results[:max_results]
        
        # Formatear respuesta
        if not results:
            return f"No se encontraron artículos académicos para la búsqueda '{query}' en las fuentes especificadas."
        
        formatted_response = f"# Resultados de Búsqueda Académica\n\n"
        formatted_response += f"**Consulta:** {query}\n"
        formatted_response += f"**Fuentes consultadas:** {source}\n"
        formatted_response += f"**Total de resultados:** {len(results)}\n\n"
        formatted_response += "---\n\n"
        
        for idx, paper in enumerate(results, 1):
            formatted_response += f"## {idx}. {paper.get('title', 'Sin título')}\n\n"
            
            if paper.get('authors'):
                authors_str = ", ".join(paper['authors'][:5])
                if len(paper['authors']) > 5:
                    authors_str += f" et al. ({len(paper['authors'])} autores)"
                formatted_response += f"**Autores:** {authors_str}\n\n"
            
            if paper.get('published_date'):
                formatted_response += f"**Fecha de publicación:** {paper['published_date']}\n\n"
            
            if paper.get('source'):
                formatted_response += f"**Fuente:** {paper['source']}\n\n"
            
            if paper.get('abstract'):
                abstract = paper['abstract'][:500] + "..." if len(paper['abstract']) > 500 else paper['abstract']
                formatted_response += f"**Resumen:** {abstract}\n\n"
            
            if paper.get('citations') is not None:
                formatted_response += f"**Citas:** {paper['citations']}\n\n"
            
            if paper.get('url'):
                formatted_response += f"**Enlace:** {paper['url']}\n\n"
            
            if paper.get('doi'):
                formatted_response += f"**DOI:** {paper['doi']}\n\n"
            
            formatted_response += "---\n\n"
        
        print(f"Retornando {len(results)} artículos académicos")
        return formatted_response
        
    except Exception as e:
        print(f"Error en search_academic_papers: {e}")
        return f"Error al buscar artículos académicos: {str(e)}"


def _search_arxiv(query: str, max_results: int, sort_by: str) -> List[dict]:
    """Busca artículos en ArXiv usando su API pública."""
    try:
        # ArXiv API endpoint
        base_url = "http://export.arxiv.org/api/query"
        
        # Parámetros de búsqueda
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance" if sort_by == "relevance" else "submittedDate",
            "sortOrder": "descending" if sort_by == "date" else "ascending"
        }
        
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        
        # Parsear XML
        root = ET.fromstring(response.content)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        papers = []
        for entry in root.findall('atom:entry', ns):
            paper = {
                'title': entry.find('atom:title', ns).text.strip().replace('\n', ' ') if entry.find('atom:title', ns) is not None else '',
                'authors': [author.find('atom:name', ns).text for author in entry.findall('atom:author', ns) if author.find('atom:name', ns) is not None],
                'abstract': entry.find('atom:summary', ns).text.strip().replace('\n', ' ') if entry.find('atom:summary', ns) is not None else '',
                'published_date': entry.find('atom:published', ns).text[:10] if entry.find('atom:published', ns) is not None else '',
                'url': entry.find('atom:id', ns).text if entry.find('atom:id', ns) is not None else '',
                'source': 'ArXiv',
                'citations': None  # ArXiv no proporciona citas directamente
            }
            
            # Extraer DOI si está disponible
            for link in entry.findall('atom:link', ns):
                if link.get('title') == 'doi':
                    paper['doi'] = link.get('href', '')
                    break
            
            papers.append(paper)
        
        return papers
        
    except Exception as e:
        print(f"Error en _search_arxiv: {e}")
        return []


def _search_researchgate(query: str, max_results: int, sort_by: str) -> List[dict]:
    """Busca artículos en ResearchGate usando web scraping."""
    try:
        # ResearchGate search URL
        search_url = f"https://www.researchgate.net/search?q={urllib.parse.quote(query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        papers = []
        
        # ResearchGate estructura puede variar, buscamos elementos comunes
        # Nota: ResearchGate puede requerir autenticación o tener protección anti-scraping
        paper_elements = soup.find_all('div', class_='nova-legacy-c-card', limit=max_results)
        
        for element in paper_elements:
            try:
                title_elem = element.find('h2', class_='nova-legacy-e-text') or element.find('a', class_='nova-legacy-e-link')
                title = title_elem.get_text(strip=True) if title_elem else 'Sin título'
                
                # Buscar enlace
                link_elem = element.find('a', href=True)
                url = f"https://www.researchgate.net{link_elem['href']}" if link_elem and link_elem.get('href') else ''
                
                # Buscar autores
                authors = []
                author_elems = element.find_all('a', class_='nova-legacy-e-link')
                for author_elem in author_elems[:5]:
                    author_text = author_elem.get_text(strip=True)
                    if author_text and len(author_text) < 50:  # Filtrar textos muy largos
                        authors.append(author_text)
                
                # Buscar fecha
                date_elem = element.find('time') or element.find('span', class_='nova-legacy-c-badge')
                published_date = date_elem.get_text(strip=True) if date_elem else ''
                
                # Buscar resumen/preview
                abstract_elem = element.find('div', class_='nova-legacy-e-text--spacing-xs')
                abstract = abstract_elem.get_text(strip=True)[:500] if abstract_elem else ''
                
                if title and title != 'Sin título':
                    papers.append({
                        'title': title,
                        'authors': authors,
                        'abstract': abstract,
                        'published_date': published_date,
                        'url': url,
                        'source': 'ResearchGate',
                        'citations': None
                    })
            except Exception as e:
                print(f"Error procesando elemento de ResearchGate: {e}")
                continue
        
        return papers[:max_results]
        
    except Exception as e:
        print(f"Error en _search_researchgate: {e}")
        return []


def _search_scopus(query: str, max_results: int, sort_by: str) -> List[dict]:
    """Busca artículos en Scopus usando búsqueda web básica."""
    try:
        # Scopus tiene una API pero requiere autenticación
        # Usaremos búsqueda web básica como alternativa
        search_url = f"https://www.scopus.com/results/results.uri?sort=plf-f&src=s&st1={urllib.parse.quote(query)}&nlo=&nlr=&nls=&sid=basic-search&origin=searchbasic&editSaveSearch=&txGid=0"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        papers = []
        
        # Scopus puede requerir autenticación, así que intentamos extraer lo que podamos
        # Nota: Scopus tiene protección anti-scraping, así que esto puede no funcionar completamente
        paper_elements = soup.find_all('tr', class_='searchArea', limit=max_results) or soup.find_all('div', class_='search-result-item', limit=max_results)
        
        for element in paper_elements:
            try:
                title_elem = element.find('a', class_='anchorText') or element.find('h3') or element.find('a', href=True)
                title = title_elem.get_text(strip=True) if title_elem else 'Sin título'
                
                # Buscar enlace
                link_elem = element.find('a', href=True)
                url = f"https://www.scopus.com{link_elem['href']}" if link_elem and link_elem.get('href') and not link_elem['href'].startswith('http') else (link_elem['href'] if link_elem and link_elem.get('href') else '')
                
                # Buscar autores
                authors = []
                author_elem = element.find('span', class_='anchorText')
                if author_elem:
                    authors_text = author_elem.get_text(strip=True)
                    authors = [a.strip() for a in authors_text.split(',')[:5]]
                
                # Buscar año
                year_elem = element.find('span', class_='anchorText')
                published_date = year_elem.get_text(strip=True) if year_elem else ''
                
                # Buscar citas si están disponibles
                citations_elem = element.find('span', string=lambda x: x and 'cited' in x.lower())
                citations = None
                if citations_elem:
                    try:
                        citations_text = citations_elem.get_text(strip=True)
                        citations = int(''.join(filter(str.isdigit, citations_text)))
                    except:
                        pass
                
                if title and title != 'Sin título':
                    papers.append({
                        'title': title,
                        'authors': authors,
                        'abstract': '',
                        'published_date': published_date,
                        'url': url,
                        'source': 'Scopus',
                        'citations': citations
                    })
            except Exception as e:
                print(f"Error procesando elemento de Scopus: {e}")
                continue
        
        return papers[:max_results]
        
    except Exception as e:
        print(f"Error en _search_scopus: {e}")
        return []


def web_search(
    query: Annotated[str, "Consulta de búsqueda en la web"],
    max_results: Annotated[int, "Número máximo de resultados a retornar (máximo 20)"] = 10
):
    """
    Realiza una búsqueda en la web utilizando Google Search y retorna los resultados más relevantes.
    
    Esta herramienta permite buscar información actualizada de internet, noticias, artículos,
    documentación técnica, y cualquier contenido disponible en la web.
    """
    try:
        print(f"Buscando en la web: '{query}'")
        
        # Limitar max_results
        max_results = min(max_results, 20)
        
        # Intentar usar Google Custom Search API si está configurada
        google_api_key = os.getenv("GOOGLE_API_KEY")
        google_cse_id = os.getenv("GOOGLE_CSE_ID")
        
        if google_api_key and google_cse_id:
            try:
                results = _search_google_custom_search(query, google_api_key, google_cse_id, max_results)
                if results:
                    print(f"Encontrados {len(results)} resultados usando Google Custom Search")
                    return _format_web_search_results(query, results)
            except Exception as e:
                print(f"Error con Google Custom Search, usando fallback: {e}")
        
        # Fallback: usar DuckDuckGo HTML (sin API key requerida)
        try:
            results = _search_duckduckgo(query, max_results)
            if results:
                print(f"Encontrados {len(results)} resultados usando DuckDuckGo")
                return _format_web_search_results(query, results)
        except Exception as e:
            print(f"Error con DuckDuckGo: {e}")
        
        # Último fallback: búsqueda básica con Google (sin API)
        try:
            results = _search_google_basic(query, max_results)
            if results:
                print(f"Encontrados {len(results)} resultados usando búsqueda básica")
                return _format_web_search_results(query, results)
        except Exception as e:
            print(f"Error con búsqueda básica: {e}")
        
        return f"No se pudieron obtener resultados de búsqueda web para '{query}'. Por favor, intenta con una consulta diferente."
        
    except Exception as e:
        print(f"Error en web_search: {e}")
        return f"Error al realizar búsqueda web: {str(e)}"


def _search_google_custom_search(query: str, api_key: str, cse_id: str, max_results: int) -> List[dict]:
    """Busca usando Google Custom Search API."""
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": min(max_results, 10)  # Google permite máximo 10 por request
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        results = []
        
        for item in data.get("items", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "display_url": item.get("displayLink", ""),
                "source": "Google Search"
            })
        
        return results
        
    except Exception as e:
        print(f"Error en _search_google_custom_search: {e}")
        return []


def _search_duckduckgo(query: str, max_results: int) -> List[dict]:
    """Busca usando DuckDuckGo HTML (sin API key requerida)."""
    try:
        # DuckDuckGo HTML search
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        results = []
        
        # Buscar resultados en DuckDuckGo
        result_elements = soup.find_all('div', class_='result', limit=max_results)
        
        for element in result_elements:
            try:
                title_elem = element.find('a', class_='result__a')
                title = title_elem.get_text(strip=True) if title_elem else 'Sin título'
                url = title_elem.get('href', '') if title_elem else ''
                
                snippet_elem = element.find('a', class_='result__snippet')
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''
                
                if title and title != 'Sin título' and url:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet[:300],  # Limitar snippet
                        "display_url": url.split('/')[2] if url.startswith('http') else url,
                        "source": "DuckDuckGo"
                    })
            except Exception as e:
                print(f"Error procesando resultado de DuckDuckGo: {e}")
                continue
        
        return results
        
    except Exception as e:
        print(f"Error en _search_duckduckgo: {e}")
        return []


def _search_google_basic(query: str, max_results: int) -> List[dict]:
    """Búsqueda básica usando Google HTML (limitada por protección anti-scraping)."""
    try:
        search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num={max_results}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        results = []
        
        # Google puede tener diferentes estructuras, intentamos múltiples selectores
        result_elements = (
            soup.find_all('div', class_='g', limit=max_results) or
            soup.find_all('div', class_='tF2Cxc', limit=max_results) or
            soup.find_all('div', {'data-ved': True}, limit=max_results)
        )
        
        for element in result_elements[:max_results]:
            try:
                # Buscar título
                title_elem = (
                    element.find('h3') or
                    element.find('a', {'data-ved': True}) or
                    element.find('a', href=True)
                )
                title = title_elem.get_text(strip=True) if title_elem else 'Sin título'
                
                # Buscar URL
                link_elem = element.find('a', href=True)
                url = ''
                if link_elem:
                    href = link_elem.get('href', '')
                    if href.startswith('/url?q='):
                        url = urllib.parse.unquote(href.split('/url?q=')[1].split('&')[0])
                    elif href.startswith('http'):
                        url = href
                
                # Buscar snippet
                snippet_elem = (
                    element.find('span', class_='aCOpRe') or
                    element.find('div', class_='VwiC3b') or
                    element.find('span', class_='st')
                )
                snippet = snippet_elem.get_text(strip=True)[:300] if snippet_elem else ''
                
                if title and title != 'Sin título' and url:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "display_url": url.split('/')[2] if url.startswith('http') else url,
                        "source": "Google"
                    })
            except Exception as e:
                print(f"Error procesando resultado de Google: {e}")
                continue
        
        return results
        
    except Exception as e:
        print(f"Error en _search_google_basic: {e}")
        return []


def _format_web_search_results(query: str, results: List[dict]) -> str:
    """Formatea los resultados de búsqueda web en Markdown."""
    if not results:
        return f"No se encontraron resultados para la búsqueda '{query}'."
    
    formatted_response = f"# Resultados de Búsqueda Web\n\n"
    formatted_response += f"**Consulta:** {query}\n"
    formatted_response += f"**Total de resultados:** {len(results)}\n\n"
    formatted_response += "---\n\n"
    
    for idx, result in enumerate(results, 1):
        formatted_response += f"## {idx}. {result.get('title', 'Sin título')}\n\n"
        
        if result.get('url'):
            formatted_response += f"**URL:** {result['url']}\n\n"
        
        if result.get('display_url'):
            formatted_response += f"**Fuente:** {result['display_url']}\n\n"
        
        if result.get('snippet'):
            formatted_response += f"**Resumen:** {result['snippet']}\n\n"
        
        formatted_response += "---\n\n"
    
    return formatted_response