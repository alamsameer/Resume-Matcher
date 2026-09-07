"""Print-page fallback for PDF generation when the frontend SSR is unavailable.

Playwright on Render navigates to FRONTEND_BASE_URL/print/resumes/{id}. When that
origin is the backend itself (or the Vercel print page is broken), this route
returns a self-contained HTML document with the `.resume-print` marker the PDF
renderer waits for.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["print"])


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _render_list(items: list[Any] | None) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(item)}</li>" for item in items if item)
    return f"<ul>{lis}</ul>" if lis else ""


def _render_resume_html(data: dict[str, Any], page_size: str) -> str:
    personal = data.get("personalInfo") or {}
    name = _esc(personal.get("name") or "Resume")
    email = _esc(personal.get("email") or "")
    phone = _esc(personal.get("phone") or "")
    location = _esc(personal.get("location") or "")
    summary = _esc(data.get("summary") or "")

    experience_blocks: list[str] = []
    for exp in data.get("workExperience") or []:
        company = _esc(exp.get("company") or "")
        position = _esc(exp.get("position") or exp.get("title") or "")
        start = _esc(exp.get("startDate") or "")
        end = _esc(exp.get("endDate") or "Present")
        desc = _render_list(exp.get("description") or exp.get("highlights"))
        experience_blocks.append(
            f"<section><h3>{position} — {company}</h3>"
            f"<p class='meta'>{start} – {end}</p>{desc}</section>"
        )

    education_blocks: list[str] = []
    for edu in data.get("education") or []:
        institution = _esc(edu.get("institution") or edu.get("school") or "")
        degree = _esc(edu.get("degree") or "")
        education_blocks.append(f"<section><h3>{degree}</h3><p>{institution}</p></section>")

    skills_html = ""
    skills = data.get("skills") or []
    if isinstance(skills, list):
        skill_bits: list[str] = []
        for skill in skills:
            if isinstance(skill, dict):
                skill_bits.extend(str(s) for s in (skill.get("keywords") or skill.get("items") or []) if s)
            elif skill:
                skill_bits.append(str(skill))
        if skill_bits:
            skills_html = "<p>" + ", ".join(_esc(s) for s in skill_bits) + "</p>"

    page_css = "A4" if page_size.upper() == "A4" else "Letter"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{name}</title>
  <style>
    @page {{ size: {page_css}; margin: 12mm; }}
    body {{ font-family: Georgia, 'Times New Roman', serif; color: #000; margin: 0; padding: 24px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    h2 {{ font-size: 16px; border-bottom: 1px solid #000; margin: 20px 0 8px; text-transform: uppercase; letter-spacing: 0.04em; }}
    h3 {{ font-size: 14px; margin: 12px 0 4px; }}
    .meta, .contact {{ font-size: 12px; color: #333; margin: 0 0 4px; }}
    ul {{ margin: 4px 0 0 18px; padding: 0; }}
    li {{ margin: 2px 0; font-size: 13px; }}
    p {{ font-size: 13px; line-height: 1.4; margin: 0 0 8px; }}
  </style>
</head>
<body>
  <article class="resume-print">
    <header>
      <h1>{name}</h1>
      <p class="contact">{email} · {phone} · {location}</p>
    </header>
    {"<h2>Summary</h2><p>" + summary + "</p>" if summary else ""}
    {"<h2>Experience</h2>" + "".join(experience_blocks) if experience_blocks else ""}
    {"<h2>Education</h2>" + "".join(education_blocks) if education_blocks else ""}
    {"<h2>Skills</h2>" + skills_html if skills_html else ""}
  </article>
</body>
</html>"""


@router.get("/resumes/{resume_id}", response_class=HTMLResponse)
async def print_resume(
    resume_id: str,
    pageSize: str = Query("A4"),
) -> HTMLResponse:
    """Server-rendered print HTML used by Playwright PDF generation."""
    resume = await db.get_resume(resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    data = resume.get("processed_data")
    if not data:
        content = resume.get("content") or ""
        try:
            import json

            parsed = json.loads(content)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            data = None

    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Resume has no structured data to print")

    html_doc = _render_resume_html(data, pageSize)
    return HTMLResponse(content=html_doc, status_code=200)
