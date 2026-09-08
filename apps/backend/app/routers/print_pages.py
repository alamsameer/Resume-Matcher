"""Print-page HTML for PDF generation (Playwright target).

Renders the same ResumeData fields as the swiss-single UI template so downloaded
PDFs include projects, skills/awards, and a comparable single-column layout when
FRONTEND_BASE_URL points at this backend.
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


def _clean_strings(items: list[Any] | None) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for item in items:
        text = str(item).strip() if item is not None else ""
        if text:
            out.append(text)
    return out


def _render_list(items: list[Any] | None) -> str:
    cleaned = _clean_strings(items)
    if not cleaned:
        return ""
    lis = "".join(f"<li>{_esc(item)}</li>" for item in cleaned)
    return f"<ul>{lis}</ul>"


def _date_range(*parts: Any) -> str:
    """Prefer a preformatted years string; else join start/end."""
    if not parts:
        return ""
    first = parts[0]
    if isinstance(first, str) and first.strip():
        text = first.strip()
        # UI stores display ranges in `years` (e.g. "Oct 2023 - Present")
        if len(parts) == 1 or any(sep in text for sep in ("-", "–", "—")):
            return text.replace(" - ", " – ").replace("-", "–") if " – " not in text else text
    start_s = str(parts[0]).strip() if parts[0] else ""
    end_s = str(parts[1]).strip() if len(parts) > 1 and parts[1] else "Present"
    if not start_s:
        return end_s if end_s else ""
    return f"{start_s} – {end_s}"


def _section(title: str, body: str) -> str:
    if not body.strip():
        return ""
    return f"<section class='section'><h2>{_esc(title)}</h2>{body}</section>"


def _render_resume_html(data: dict[str, Any], page_size: str) -> str:
    personal = data.get("personalInfo") or {}
    name = _esc(personal.get("name") or "Resume")
    title = _esc(personal.get("title") or personal.get("headline") or "")
    email = personal.get("email") or ""
    phone = personal.get("phone") or ""
    location = personal.get("location") or ""
    website = personal.get("website") or ""
    linkedin = personal.get("linkedin") or personal.get("linkedIn") or ""
    github = personal.get("github") or ""

    contact_bits = [
        _esc(v)
        for v in (email, phone, location, website, linkedin, github)
        if v and str(v).strip()
    ]
    contact_html = " · ".join(contact_bits)

    summary = data.get("summary") or ""
    summary_html = f"<p class='summary'>{_esc(summary)}</p>" if summary else ""

    # Experience — UI uses title/company/location/years
    experience_blocks: list[str] = []
    for exp in data.get("workExperience") or []:
        position = _esc(exp.get("title") or exp.get("position") or "")
        company = _esc(exp.get("company") or "")
        loc = _esc(exp.get("location") or "")
        if exp.get("years"):
            dates = _esc(_date_range(exp.get("years")))
        else:
            dates = _esc(_date_range(exp.get("startDate"), exp.get("endDate")))
        desc = _render_list(exp.get("description") or exp.get("highlights"))
        company_line = " | ".join(p for p in (company, loc) if p)
        experience_blocks.append(
            "<div class='item'>"
            f"<div class='row'><h3>{position}</h3>"
            f"<span class='date'>{dates}</span></div>"
            f"{f'<p class=\"meta\">{company_line}</p>' if company_line else ''}"
            f"{desc}</div>"
        )

    # Education
    education_blocks: list[str] = []
    for edu in data.get("education") or []:
        institution = _esc(edu.get("institution") or edu.get("school") or "")
        degree = _esc(edu.get("degree") or "")
        loc = _esc(edu.get("location") or "")
        if edu.get("years"):
            dates = _esc(_date_range(edu.get("years")))
        else:
            dates = _esc(_date_range(edu.get("startDate"), edu.get("endDate")))
        school_line = " | ".join(p for p in (institution, loc) if p)
        education_blocks.append(
            "<div class='item'>"
            f"<div class='row'><h3>{degree or institution}</h3>"
            f"<span class='date'>{dates}</span></div>"
            f"{f'<p class=\"meta\">{school_line}</p>' if school_line else ''}"
            f"</div>"
        )

    # Projects (UI field: personalProjects) — name/role/years/description
    project_blocks: list[str] = []
    for project in data.get("personalProjects") or []:
        pname = _esc(project.get("name") or "")
        role = project.get("role") or ""
        years_raw = project.get("years") or ""
        dates = _esc(_date_range(years_raw)) if years_raw else ""
        github_url = project.get("github") or ""
        website_url = project.get("website") or ""
        links = " · ".join(
            _esc(u) for u in (github_url, website_url) if u and str(u).strip()
        )
        desc = _render_list(project.get("description") or project.get("highlights"))
        role_html = f"<p class='tech'>{_esc(role)}</p>" if role else ""
        project_blocks.append(
            "<div class='item'>"
            f"<div class='row'><h3>{pname}</h3><span class='date'>{dates}</span></div>"
            f"{role_html}"
            f"{f'<p class=\"meta\">{links}</p>' if links else ''}"
            f"{desc}</div>"
        )
    # Skills & Awards (UI field: additional.*)
    additional = data.get("additional") or {}
    skills_lines: list[str] = []
    tech_skills = _clean_strings(additional.get("technicalSkills"))
    languages = _clean_strings(additional.get("languages"))
    certs = _clean_strings(additional.get("certificationsTraining"))
    awards = _clean_strings(additional.get("awards"))

    # Legacy top-level skills fallback
    if not tech_skills:
        raw_skills = data.get("skills") or []
        if isinstance(raw_skills, list):
            for skill in raw_skills:
                if isinstance(skill, dict):
                    tech_skills.extend(
                        _clean_strings(skill.get("keywords") or skill.get("items"))
                    )
                elif skill:
                    tech_skills.append(str(skill).strip())

    if tech_skills:
        skills_lines.append(
            "<p><strong>Technical Skills:</strong> "
            + _esc(", ".join(tech_skills))
            + "</p>"
        )
    if languages:
        skills_lines.append(
            "<p><strong>Languages:</strong> " + _esc(", ".join(languages)) + "</p>"
        )
    if certs:
        skills_lines.append(
            "<p><strong>Certifications:</strong> " + _esc(", ".join(certs)) + "</p>"
        )
    if awards:
        skills_lines.append(
            "<p><strong>Awards:</strong> " + _esc(", ".join(awards)) + "</p>"
        )

    page_css = "A4" if page_size.upper() == "A4" else "Letter"
    title_block = f"<p class='job-title'>{title}</p>" if title else ""

    body = "".join(
        [
            _section("Summary", summary_html),
            _section("Experience", "".join(experience_blocks)),
            _section("Education", "".join(education_blocks)),
            _section("Projects", "".join(project_blocks)),
            _section("Skills & Awards", "".join(skills_lines)),
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{name}</title>
  <style>
    @page {{ size: {page_css}; margin: 12mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Times New Roman", Times, Georgia, serif;
      color: #000;
      margin: 0;
      padding: 18px 22px;
      font-size: 11pt;
      line-height: 1.35;
    }}
    header {{ text-align: center; margin-bottom: 14px; }}
    h1 {{
      font-size: 22pt;
      margin: 0 0 4px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      font-weight: 700;
    }}
    .job-title {{
      margin: 0 0 6px;
      font-size: 11pt;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .contact {{ margin: 0; font-size: 10pt; }}
    h2 {{
      font-size: 11pt;
      margin: 16px 0 6px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      border-bottom: 1px solid #000;
      padding-bottom: 2px;
    }}
    h3 {{ font-size: 11pt; margin: 0; font-weight: 700; }}
    .section {{ margin: 0; }}
    .item {{ margin: 0 0 10px; }}
    .row {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
    }}
    .date, .meta, .tech {{
      font-size: 10pt;
      margin: 2px 0 4px;
      color: #111;
    }}
    .date {{ white-space: nowrap; font-weight: 400; }}
    .tech {{ font-style: italic; }}
    .summary {{ margin: 0; text-align: justify; }}
    ul {{ margin: 4px 0 0 18px; padding: 0; }}
    li {{ margin: 2px 0; }}
  </style>
</head>
<body>
  <article class="resume-print">
    <header>
      <h1>{name}</h1>
      {title_block}
      <p class="contact">{contact_html}</p>
    </header>
    {body}
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
