"""
Generación de evaluaciones con LLM a partir de documentos e imágenes, y calificación con retroalimentación.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from langchain_google_genai import ChatGoogleGenerativeAI

from app.document_loaders import get_docs
from app.config import get_settings


def _file_path_to_url(file_path: str) -> str:
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
    if ext == ".docx":
        return "docx"
    if ext == ".doc":
        return "doc"
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return "img"
    return "pdf"


def _extract_combined_content(file_paths: List[Tuple[str, str]]) -> str:
    all_content: List[str] = []
    for file_path, filename in file_paths:
        if not os.path.exists(file_path):
            continue
        try:
            file_type = _get_file_type(filename)
            url = _file_path_to_url(file_path)
            if file_type == "img":
                content = get_docs(
                    url,
                    file_type,
                    query="Extrae y resume el contenido textual o conceptual visible en la imagen.",
                    verbose=False,
                )
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
        return ""
    return "\n\n---\n\n".join(all_content)[:15000]


def _parse_json_from_llm(text: str) -> Dict[str, Any]:
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        raw = m.group(1).strip()
    return json.loads(raw)


def _normalize_questions(raw_questions: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_questions, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(raw_questions):
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or f"q{i+1}")
        qtype = (q.get("type") or "open").strip().lower()
        if qtype not in ("multiple_choice", "open"):
            qtype = "open"
        item: Dict[str, Any] = {"id": qid, "type": qtype, "question": str(q.get("question") or "").strip()}
        if qtype == "multiple_choice":
            opts = q.get("options")
            if not isinstance(opts, list):
                opts = []
            opts = [str(o).strip() for o in opts if str(o).strip()]
            if len(opts) < 2:
                continue
            ci = q.get("correct_index")
            try:
                ci = int(ci)
            except (TypeError, ValueError):
                ci = 0
            ci = max(0, min(ci, len(opts) - 1))
            item["options"] = opts
            item["correct_index"] = ci
        else:
            rubric = q.get("rubric")
            item["rubric"] = str(rubric).strip() if rubric else ""
        out.append(item)
    return out


def generate_evaluation_from_files(
    file_paths: List[Tuple[str, str]],
    requirements: str = "",
) -> Dict[str, Any]:
    combined = _extract_combined_content(file_paths)
    if not combined.strip():
        raise ValueError("No se pudo extraer contenido de los archivos")

    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.4)

    prompt = f"""Eres un diseñador instruccional. A partir del material de referencia y los requisitos del usuario,
genera una evaluación en ESPAÑOL con entre 5 y 12 preguntas, mezclando:
- "multiple_choice": 3 o 4 opciones, una correcta (índice 0-based en correct_index)
- "open": respuesta corta o desarrollo, con un "rubric" breve (1-2 frases) para orientar la calificación

Responde ÚNICAMENTE con un JSON válido con esta forma exacta (sin markdown fuera del JSON):
{{
  "title": "título corto de la evaluación",
  "description": "1-3 frases sobre qué mide",
  "questions": [
    {{
      "id": "q1",
      "type": "multiple_choice",
      "question": "enunciado",
      "options": ["opción A", "opción B", "opción C"],
      "correct_index": 0
    }},
    {{
      "id": "q2",
      "type": "open",
      "question": "enunciado",
      "rubric": "criterios de respuesta esperada"
    }}
  ]
}}

REQUISITOS DEL USUARIO (prioridad alta):
{requirements or "Evaluación equilibrada según el material."}

MATERIAL DE REFERENCIA:
{combined}
"""

    response = llm.invoke(prompt)
    text = (response.content or "").strip()
    data = _parse_json_from_llm(text)
    title = str(data.get("title") or "Evaluación").strip() or "Evaluación"
    description = str(data.get("description") or "").strip()
    questions = _normalize_questions(data.get("questions"))
    if len(questions) < 3:
        raise ValueError("El modelo generó muy pocas preguntas válidas; intenta de nuevo o ajusta los requisitos.")
    return {"title": title, "description": description, "questions": questions}


def strip_questions_for_student(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Oculta respuestas correctas y rúbricas detalladas para quien responde."""
    out: List[Dict[str, Any]] = []
    for q in questions:
        qcopy = {"id": q["id"], "type": q["type"], "question": q["question"]}
        if q.get("type") == "multiple_choice" and q.get("options"):
            qcopy["options"] = list(q["options"])
        out.append(qcopy)
    return out


def _score_mcq(q: Dict[str, Any], answer: Any) -> float:
    try:
        idx = int(answer)
    except (TypeError, ValueError):
        return 0.0
    correct = q.get("correct_index")
    try:
        correct = int(correct)
    except (TypeError, ValueError):
        return 0.0
    return 1.0 if idx == correct else 0.0


def _llm_score_open_questions(
    items: List[Tuple[Dict[str, Any], str]],
) -> Dict[str, float]:
    if not items:
        return {}
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.2)
    lines = []
    for q, ans in items:
        lines.append(
            f"PREGUNTA_ID: {q['id']}\nPREGUNTA: {q['question']}\nRÚBRICA: {q.get('rubric', '')}\nRESPUESTA_ESTUDIANTE: {ans}\n---"
        )
    prompt = f"""Califica cada respuesta abierta de 0.0 a 1.0 según la rúbrica y la pregunta.
Responde SOLO JSON: {{"scores": {{"q1": 0.75, "q2": 1.0}}}}

{chr(10).join(lines)}
"""
    response = llm.invoke(prompt)
    text = (response.content or "").strip()
    data = _parse_json_from_llm(text)
    scores = data.get("scores")
    if not isinstance(scores, dict):
        return {q["id"]: 0.5 for q, _ in items}
    out: Dict[str, float] = {}
    for q, _ in items:
        sid = q["id"]
        v = scores.get(sid)
        try:
            fv = float(v)
        except (TypeError, ValueError):
            fv = 0.5
        out[sid] = max(0.0, min(1.0, fv))
    return out


def _fallback_feedback_only(
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
    per_question_scores: Dict[str, float],
    total_percent: float,
) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.5)
    summary_bits = []
    for q in questions:
        qid = q["id"]
        summary_bits.append(
            f"- [{qid}] ({q.get('type')}) puntaje relativo: {per_question_scores.get(qid, 0):.2f}\n  Respuesta: {answers.get(qid)!r}"
        )
    prompt = f"""Puntuación total (aprox.): {total_percent:.1f}%

Detalle por ítem:
{chr(10).join(summary_bits)}

Escribe retroalimentación breve y constructiva en español para el estudiante (2-4 párrafos cortos): fortalezas, áreas a mejorar y sugerencias concretas. No des la solución exacta de opción múltiple si no es necesario; enfócate en el aprendizaje."""
    response = llm.invoke(prompt)
    return (response.content or "Buen trabajo. Revisa los temas donde hubo dudas.").strip()


def _empty_performance_profile(reason: str) -> Dict[str, Any]:
    return {
        "version": "1",
        "error": reason,
        "overall": {"performance_summary": "", "relative_level": "unknown"},
        "competency_dimensions": {},
        "per_question_insights": [],
        "patterns": {"strengths": [], "weaknesses": [], "misconceptions_flagged": []},
        "study_recommendations": {"priority_topics": [], "practice_suggestions": []},
        "dashboard_charts": {"schema_version": "1.0", "error": reason},
    }


def build_dashboard_charts_payload(
    questions: List[Dict[str, Any]],
    per_question_scores: Dict[str, float],
    total_percent: float,
    *,
    duration_seconds: Optional[int] = None,
    time_limit_minutes: Optional[int] = None,
    seconds_remaining_at_submit: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Datos determinísticos listos para Recharts / ECharts / cualquier dashboard:
    arrays paralelos, porcentajes 0–100 donde el gráfico lo espera, y claves estables.
    """
    n = len(questions)
    if n == 0:
        return {
            "schema_version": "1.0",
            "summary": {
                "total_percent": round(float(total_percent), 2),
                "question_count": 0,
            },
            "bar_by_item": [],
            "radar_dimensions": [],
            "histogram_score_distribution": [],
            "comparison_mc_vs_open": [],
            "pie_question_format": [],
            "pie_multiple_choice_outcome": [],
            "pie_band_distribution": [],
            "lines_series": {"score_0_1_in_order": [], "labels_in_order": []},
            "timing": {
                "is_timed": bool(time_limit_minutes and time_limit_minutes > 0),
                "duration_seconds": duration_seconds,
                "time_limit_minutes": time_limit_minutes,
                "limit_seconds": None,
                "seconds_remaining_at_submit": seconds_remaining_at_submit,
                "time_used_ratio_0_1": None,
                "time_remaining_ratio_0_1": None,
            },
        }

    scores: List[float] = [float(per_question_scores.get(q["id"], 0.0)) for q in questions]
    mc_scores = [float(per_question_scores.get(q["id"], 0.0)) for q in questions if q.get("type") == "multiple_choice"]
    open_scores = [float(per_question_scores.get(q["id"], 0.0)) for q in questions if q.get("type") != "multiple_choice"]

    mc_avg = sum(mc_scores) / len(mc_scores) if mc_scores else None
    open_avg = sum(open_scores) / len(open_scores) if open_scores else None

    stdev = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    consistency_0_1 = max(0.0, min(1.0, 1.0 - min(1.0, stdev * 2)))

    def _pct_bucket_label(s: float) -> str:
        p = s * 100.0
        if p <= 20:
            return "0-20"
        if p <= 40:
            return "21-40"
        if p <= 60:
            return "41-60"
        if p <= 80:
            return "61-80"
        return "81-100"

    bucket_order = ["0-20", "21-40", "41-60", "61-80", "81-100"]
    bucket_counts: Dict[str, int] = {lab: 0 for lab in bucket_order}
    for s in scores:
        bucket_counts[_pct_bucket_label(s)] += 1

    histogram_for_charts = [
        {"range_label": lab, "item_count": bucket_counts[lab], "range_key": lab.replace("-", "_")}
        for lab in bucket_order
    ]

    bar_by_item: List[Dict[str, Any]] = []
    for i, q in enumerate(questions):
        qid = q["id"]
        s = float(per_question_scores.get(qid, 0.0))
        pct = round(s * 100.0, 2)
        if pct < 40:
            band = "low"
        elif pct <= 70:
            band = "mid"
        else:
            band = "high"
        qt = q.get("type") or "open"
        short_q = (q.get("question") or "")[:72]
        if len((q.get("question") or "")) > 72:
            short_q += "…"
        bar_by_item.append(
            {
                "key": qid,
                "index": i,
                "name": f"P{i + 1}",
                "name_long": short_q or qid,
                "item_type": qt,
                "score_0_1": round(s, 4),
                "score_percent": pct,
                "performance_band": band,
            }
        )

    limit_sec = int(time_limit_minutes) * 60 if time_limit_minutes and time_limit_minutes > 0 else None
    used_ratio = None
    remaining_ratio = None
    if limit_sec and limit_sec > 0 and duration_seconds is not None:
        used_ratio = max(0.0, min(1.0, float(duration_seconds) / float(limit_sec)))
    if limit_sec and limit_sec > 0 and seconds_remaining_at_submit is not None:
        remaining_ratio = max(0.0, min(1.0, float(seconds_remaining_at_submit) / float(limit_sec)))

    # Radar: ejes en escala 0–100 para Recharts (value vs fullMark)
    radar_rows: List[Dict[str, Any]] = []
    overall_100 = max(0.0, min(100.0, float(total_percent)))
    radar_rows.append(
        {
            "subject": "Promedio global",
            "value": round(overall_100, 2),
            "fullMark": 100,
            "axis_key": "overall",
        }
    )
    if mc_avg is not None:
        radar_rows.append(
            {
                "subject": "Opción múltiple (media)",
                "value": round(mc_avg * 100.0, 2),
                "fullMark": 100,
                "axis_key": "mc_avg",
            }
        )
    if open_avg is not None:
        radar_rows.append(
            {
                "subject": "Respuesta abierta (media)",
                "value": round(open_avg * 100.0, 2),
                "fullMark": 100,
                "axis_key": "open_avg",
            }
        )
    radar_rows.append(
        {
            "subject": "Consistencia entre ítems",
            "value": round(consistency_0_1 * 100.0, 2),
            "fullMark": 100,
            "axis_key": "consistency",
        }
    )

    comparison_mc_vs_open = []
    if mc_scores:
        mavg = sum(mc_scores) / len(mc_scores)
        comparison_mc_vs_open.append(
            {
                "category": "Opción múltiple",
                "avg_0_1": round(mavg, 4),
                "avg_percent": round(mavg * 100.0, 2),
                "question_count": len(mc_scores),
            }
        )
    if open_scores:
        oavg = sum(open_scores) / len(open_scores)
        comparison_mc_vs_open.append(
            {
                "category": "Abierta",
                "avg_0_1": round(oavg, 4),
                "avg_percent": round(oavg * 100.0, 2),
                "question_count": len(open_scores),
            }
        )

    mc_correct = sum(1 for s in mc_scores if s >= 0.999)
    mc_wrong = len(mc_scores) - mc_correct
    pie_question_format = [
        {"name": "Opción múltiple", "value": len(mc_scores), "segment_key": "mc"},
        {"name": "Abierta", "value": len(open_scores), "segment_key": "open"},
    ]
    pie_mc_outcome = (
        [
            {"name": "OM acertadas", "value": mc_correct, "segment_key": "mc_ok"},
            {"name": "OM falladas", "value": max(0, mc_wrong), "segment_key": "mc_fail"},
        ]
        if mc_scores
        else []
    )
    pie_band_distribution = [
        {"name": lab, "value": bucket_counts[lab], "segment_key": f"band_{lab.replace('-', '_')}"}
        for lab in bucket_order
    ]

    return {
        "schema_version": "1.0",
        "description": "Contrato estable para Recharts/ECharts: usar bar_by_item, radar_dimensions, histogram_score_distribution, comparison_mc_vs_open, timing.",
        "summary": {
            "total_percent": round(float(total_percent), 2),
            "question_count": n,
            "multiple_choice_count": len(mc_scores),
            "open_count": len(open_scores),
            "multiple_choice_avg_percent": round(mc_avg * 100.0, 2) if mc_avg is not None else None,
            "open_avg_percent": round(open_avg * 100.0, 2) if open_avg is not None else None,
            "consistency_index_0_1": round(consistency_0_1, 4),
            "score_stddev_0_1": round(stdev, 4),
        },
        "bar_by_item": bar_by_item,
        "radar_dimensions": radar_rows,
        "histogram_score_distribution": histogram_for_charts,
        "comparison_mc_vs_open": comparison_mc_vs_open,
        "pie_question_format": pie_question_format,
        "pie_multiple_choice_outcome": pie_mc_outcome,
        "pie_band_distribution": pie_band_distribution,
        "lines_series": {
            "description": "Serie en orden de aparición para LineChart / sparkline",
            "score_0_1_in_order": [round(s, 4) for s in scores],
            "labels_in_order": [b["name"] for b in bar_by_item],
        },
        "timing": {
            "is_timed": bool(time_limit_minutes and time_limit_minutes > 0),
            "duration_seconds": duration_seconds,
            "time_limit_minutes": time_limit_minutes if time_limit_minutes and time_limit_minutes > 0 else None,
            "limit_seconds": limit_sec,
            "seconds_remaining_at_submit": seconds_remaining_at_submit,
            "time_used_ratio_0_1": round(used_ratio, 4) if used_ratio is not None else None,
            "time_remaining_ratio_0_1": round(remaining_ratio, 4) if remaining_ratio is not None else None,
        },
    }


def _build_feedback_and_performance_profile(
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
    per_question_scores: Dict[str, float],
    total_percent: float,
    *,
    duration_seconds: Optional[int] = None,
    time_limit_minutes: Optional[int] = None,
    seconds_remaining_at_submit: Optional[float] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Una sola invocación LLM: retroalimentación para el alumno + JSON extenso de desempeño.
    """
    llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite-preview", temperature=0.35)
    summary_bits = []
    for q in questions:
        qid = q["id"]
        summary_bits.append(
            f"- [{qid}] tipo={q.get('type')} puntaje_relativo_0_1={per_question_scores.get(qid, 0):.4f}\n  respuesta_estudiante: {answers.get(qid)!r}"
        )
    timing_lines = []
    if time_limit_minutes and time_limit_minutes > 0:
        timing_lines.append(f"Evaluación temporizada: límite {time_limit_minutes} minutos.")
    if duration_seconds is not None:
        timing_lines.append(f"Tiempo transcurrido desde inicio de intento (si aplica): {duration_seconds} s.")
    if seconds_remaining_at_submit is not None:
        timing_lines.append(f"Segundos restantes al enviar (si temporizada): {seconds_remaining_at_submit:.0f} s.")
    timing_block = "\n".join(timing_lines) if timing_lines else "Sin datos de temporizador (evaluación sin límite o sin sesión)."

    prompt = f"""Eres un analista pedagógico. Tienes el resultado de una evaluación ya calificada (puntajes por ítem 0–1 son definitivos).

Puntuación global aproximada: {total_percent:.2f}%

Contexto de tiempo:
{timing_block}

Detalle por ítem (no cambies los puntajes; solo interprétalos):
{chr(10).join(summary_bits)}

TAREA ÚNICA — Responde SOLO con un JSON válido (sin texto antes ni después) con esta estructura exacta:
{{
  "feedback": "Retroalimentación en español para el estudiante: 2-4 párrafos cortos, tono constructivo. Fortalezas, áreas a mejorar, sugerencias. No reveles la letra correcta de opción múltiple innecesariamente.",
  "performance_profile": {{
    "version": "1",
    "overall": {{
      "performance_summary": "1-3 frases objetivas sobre el desempeño global",
      "relative_level": "insufficient|basic|proficient|advanced",
      "confidence_in_assessment": 0.0
    }},
    "score_interpretation": {{
      "multiple_choice_avg_0_1": 0.0,
      "open_response_avg_0_1": 0.0,
      "balance_comment": "comentario sobre equilibrio OM vs abiertas"
    }},
    "competency_dimensions": {{
      "conceptual_understanding_0_1": 0.0,
      "application_transfer_0_1": 0.0,
      "reasoning_argumentation_0_1": 0.0,
      "communication_clarity_0_1": 0.0
    }},
    "per_question_insights": [
      {{
        "question_id": "id",
        "item_type": "multiple_choice|open",
        "observation": "qué indica la respuesta sobre el dominio",
        "strength_or_gap": "strength|gap|mixed",
        "suggested_review_topic": "tema concreto a repasar o vacío"
      }}
    ],
    "patterns": {{
      "strengths": ["lista de fortalezas observadas"],
      "weaknesses": ["lista de debilidades o lagunas"],
      "misconceptions_flagged": ["posibles conceptos erróneos si aplica"]
    }},
    "study_recommendations": {{
      "priority_topics": ["temas prioritarios"],
      "practice_suggestions": ["cómo practicar"],
      "estimated_effort_to_improve": "low|medium|high"
    }},
    "engagement_and_pacing": {{
      "open_response_depth": "superficial|adequate|deep",
      "time_pressure_signal": "unknown|likely_relaxed|likely_rushed|likely_balanced",
      "notes": "breve nota usando contexto de tiempo si existe"
    }}
  }}
}}

Rellena números y textos según los puntajes por ítem ya dados. Las competency_dimensions deben ser coherentes con esos puntajes (0-1).
"""
    try:
        response = llm.invoke(prompt)
        text = (response.content or "").strip()
        data = _parse_json_from_llm(text)
        fb = str(data.get("feedback") or "").strip()
        perf = data.get("performance_profile")
        if not fb:
            fb = _fallback_feedback_only(questions, answers, per_question_scores, total_percent)
        if not isinstance(perf, dict):
            perf = _empty_performance_profile("performance_profile inválido en respuesta")
        else:
            perf.setdefault("version", "1")
        return fb, perf
    except Exception as e:
        fb = _fallback_feedback_only(questions, answers, per_question_scores, total_percent)
        err = _empty_performance_profile(f"parse_or_invoke: {e!s}")
        return fb, err


def build_submission_review(
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
    per_question_scores: Dict[str, float],
) -> Dict[str, Any]:
    """
    JSON para la UI tras enviar: cada ítem con enunciado, respuesta del usuario,
    respuesta correcta (OM) o rúbrica (abierta) y puntaje relativo 0–1.
    """
    items: List[Dict[str, Any]] = []
    for q in questions:
        qid = q["id"]
        qtype = q.get("type")
        score = float(per_question_scores.get(qid, 0.0))
        ua = answers.get(qid)
        row: Dict[str, Any] = {
            "id": qid,
            "type": qtype,
            "question": q.get("question") or "",
            "score": round(score, 4),
            "score_percent": round(score * 100.0, 2),
        }
        if qtype == "multiple_choice":
            opts = list(q.get("options") or [])
            ci_raw = q.get("correct_index")
            try:
                ci = int(ci_raw)
            except (TypeError, ValueError):
                ci = -1
            uidx: Optional[int] = None
            if ua is not None:
                try:
                    uidx = int(ua)
                except (TypeError, ValueError):
                    uidx = None
            correct_text = opts[ci] if 0 <= ci < len(opts) else None
            user_text = opts[uidx] if uidx is not None and 0 <= uidx < len(opts) else None
            row["options"] = opts
            row["correct_index"] = ci if 0 <= ci < len(opts) else None
            row["correct_option_text"] = correct_text
            row["user_answer_index"] = uidx
            row["user_answer_text"] = user_text
            row["is_correct"] = bool(
                uidx is not None and ci >= 0 and uidx == ci and ci < len(opts)
            )
        else:
            row["user_answer_text"] = str(ua if ua is not None else "")
            row["rubric"] = str(q.get("rubric") or "")
        items.append(row)
    return {"questions": items, "question_count": len(items)}


def grade_submission(
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
    *,
    duration_seconds: Optional[int] = None,
    time_limit_minutes: Optional[int] = None,
    seconds_remaining_at_submit: Optional[float] = None,
) -> Tuple[float, str, Dict[str, float], Dict[str, Any]]:
    """
    Devuelve (score_percent 0-100, feedback, puntajes por id 0-1, performance_profile JSON extenso del LLM).
    """
    empty_perf: Dict[str, Any] = {
        "version": "1",
        "overall": {"performance_summary": "Sin datos.", "relative_level": "unknown"},
        "note": "Evaluación vacía.",
    }
    if not questions:
        return (
            0.0,
            "No hay preguntas en esta evaluación.",
            {},
            {
                **empty_perf,
                "dashboard_charts": build_dashboard_charts_payload(
                    [],
                    {},
                    0.0,
                    duration_seconds=duration_seconds,
                    time_limit_minutes=time_limit_minutes,
                    seconds_remaining_at_submit=seconds_remaining_at_submit,
                ),
            },
        )

    per_question: Dict[str, float] = {}
    open_batch: List[Tuple[Dict[str, Any], str]] = []

    for q in questions:
        qid = q["id"]
        ans = answers.get(qid)
        if q.get("type") == "multiple_choice":
            per_question[qid] = _score_mcq(q, ans)
        else:
            text = str(ans if ans is not None else "").strip()
            open_batch.append((q, text if text else "(sin respuesta)"))

    open_scores = _llm_score_open_questions(open_batch)
    for q, _ in open_batch:
        per_question[q["id"]] = open_scores.get(q["id"], 0.5)

    n = len(questions)
    total = sum(per_question.get(q["id"], 0.0) for q in questions) / n * 100.0
    feedback, performance_profile = _build_feedback_and_performance_profile(
        questions,
        answers,
        per_question,
        total,
        duration_seconds=duration_seconds,
        time_limit_minutes=time_limit_minutes,
        seconds_remaining_at_submit=seconds_remaining_at_submit,
    )
    performance_profile["dashboard_charts"] = build_dashboard_charts_payload(
        questions,
        per_question,
        round(total, 2),
        duration_seconds=duration_seconds,
        time_limit_minutes=time_limit_minutes,
        seconds_remaining_at_submit=seconds_remaining_at_submit,
    )
    return round(total, 2), feedback, per_question, performance_profile


def validate_answers_complete(questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> None:
    missing = [q["id"] for q in questions if q["id"] not in answers]
    if missing:
        raise ValueError(f"Faltan respuestas para: {', '.join(missing)}")


def normalize_answers_for_grading(
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
) -> Dict[str, Any]:
    """Completa respuestas faltantes (p. ej. cierre por tiempo) para poder calificar."""
    out = dict(answers)
    for q in questions:
        qid = q["id"]
        if qid not in out:
            if q.get("type") == "multiple_choice":
                out[qid] = None
            else:
                out[qid] = ""
    return out
