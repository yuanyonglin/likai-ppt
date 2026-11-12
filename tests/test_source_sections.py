import asyncio
from io import BytesIO

import fitz
from docx import Document
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from landppt.services import file_processor as fp_mod
from landppt.services.source_sections import (
    parse_source_sections,
    section_image,
    sections_to_text,
)


def parse(path, name):
    return asyncio.run(parse_source_sections(str(path), name))


def text_image(text):
    """A screenshot-like PNG with Chinese text, drawn with MuPDF's built-in CJK font."""
    doc = fitz.open()
    page = doc.new_page(width=640, height=360)
    page.insert_text((80, 190), text, fontname="china-s", fontsize=40)
    return page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")


def make_deck(path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "核心指标"
    data = CategoryChartData()
    data.categories = ["Q1", "Q2"]
    data.add_series("营收", (12, 15))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7), Inches(2), Inches(5), Inches(4), data)
    table = s.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(5), Inches(1)).table
    for c, v in enumerate(["区域", "增速"]):
        table.cell(0, c).text = v
    for c, v in enumerate(["华东", "21%"]):
        table.cell(1, c).text = v
    s.notes_slide.notes_text_frame.text = "重点讲 Q4"

    # Three columns, one box per line, boxes spilling into the next column as in
    # exported decks: rows interleave unless grouped by left edge.
    s = prs.slides.add_slide(prs.slide_layouts[6])
    banner = s.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12), Inches(0.8))
    banner.text_frame.text = "三个痛点"
    for col, name in enumerate(["换线", "数据", "异常"]):
        x = Inches(0.6 + col * 4.2)
        s.shapes.add_textbox(x + Inches(0.8), Inches(1.5), Inches(2), Inches(0.5)).text_frame.text = f"{name}标题"
        for row in range(2):
            s.shapes.add_textbox(x, Inches(2.2 + row * 0.5), Inches(5), Inches(0.5)).text_frame.text = f"{name}第{row + 1}行"

    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_picture(BytesIO(text_image("图片页文字")), 0, 0, prs.slide_width, prs.slide_height)
    prs.save(path)


def test_pptx_sections_have_title_body_notes_and_chart(tmp_path):
    path = tmp_path / "deck.pptx"
    make_deck(path)
    sections = parse(path, "deck.pptx")

    assert [s["index"] for s in sections] == [0, 1, 2]
    first = sections[0]
    assert first["title"] == "核心指标" and not first["image_only"]
    assert "区域 | 增速" in first["text"] and "华东 | 21%" in first["text"]
    assert "营收：Q1 12.0，Q2 15.0" in first["text"]
    assert first["text"].endswith("备注：重点讲 Q4")


def test_pptx_multi_column_reads_column_by_column(tmp_path):
    path = tmp_path / "deck.pptx"
    make_deck(path)
    lines = parse(path, "deck.pptx")[1]["text"].splitlines()

    assert lines == [
        "三个痛点",
        "换线标题", "换线第1行", "换线第2行",
        "数据标题", "数据第1行", "数据第2行",
        "异常标题", "异常第1行", "异常第2行",
    ]


def test_pptx_picture_only_slide_is_ocr_marked(tmp_path):
    path = tmp_path / "deck.pptx"
    make_deck(path)
    page = parse(path, "deck.pptx")[2]

    assert page["image_only"] is True
    assert "图片页" in page["text"]
    assert section_image(str(path), "deck.pptx", 2)[:4] == b"\x89PNG"


def test_pdf_splits_by_page(tmp_path):
    doc = fitz.open()
    for title in ["Overview", "Results"]:
        doc.new_page().insert_text((72, 72), f"{title}\nbody of {title}")
    path = tmp_path / "a.pdf"
    doc.save(path)

    sections = parse(path, "a.pdf")
    assert [(s["index"], s["title"]) for s in sections] == [(0, "Overview"), (1, "Results")]
    assert "body of Results" in sections[1]["text"]


def test_docx_splits_at_highest_level_with_several_headings(tmp_path):
    doc = Document()
    doc.add_heading("年度报告", 0)
    doc.add_paragraph("导语")
    doc.add_heading("背景", 1)
    doc.add_paragraph("背景正文")
    doc.add_heading("细节", 2)
    doc.add_paragraph("细节正文")
    doc.add_heading("成效", 1)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "指标", "数值"
    path = tmp_path / "r.docx"
    doc.save(path)

    sections = parse(path, "r.docx")
    assert [s["title"] for s in sections] == ["年度报告", "背景", "成效"]
    assert "细节\n细节正文" in sections[1]["text"]
    assert sections[2]["text"] == "指标 | 数值"


def test_markdown_ignores_fenced_hashes_and_keeps_lead_text(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("前言\n\n# 一\nA\n```\n# not heading\n```\n# 二\nB\n", encoding="utf-8")

    sections = parse(path, "notes.md")
    assert [s["title"] for s in sections] == ["notes", "一", "二"]
    assert "# not heading" in sections[1]["text"]
    assert "## 第 2 节：一" in sections_to_text(sections, "节")


def test_file_without_headings_is_one_section(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("只有正文", encoding="utf-8")
    assert parse(path, "plain.md") == [{"index": 0, "title": "plain", "text": "只有正文", "image_only": False}]


def test_existing_upload_accepts_pptx(tmp_path):
    path = tmp_path / "deck.pptx"
    make_deck(path)
    processor = fp_mod.FileProcessor()

    assert processor.validate_file("deck.pptx", 1024)[0] is True
    result = asyncio.run(processor.process_file(str(path), "deck.pptx"))
    assert "## 第 1 页：核心指标" in result.processed_content
    assert "备注：重点讲 Q4" in result.processed_content


def test_describe_section_image_uses_vision_role_only_when_configured(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from landppt.services import db_config_service, service_instances
    from landppt.services.source_sections import describe_section_image

    path = tmp_path / "deck.pptx"
    make_deck(path)
    config, calls = {}, []

    class Config:
        async def get_all_config(self, user_id):
            return config

    class Service:
        async def _chat_completion_for_role(self, role, *, messages):
            calls.append((role, messages[0].content[1].image_url["url"]))
            return SimpleNamespace(content=" 图片页文字 ")

    monkeypatch.setattr(db_config_service, "get_db_config_service", lambda: Config())
    monkeypatch.setattr(service_instances, "get_ppt_service_for_user", lambda user_id: Service())

    assert asyncio.run(describe_section_image(str(path), "deck.pptx", 2, 1)) is None
    config.update(vision_analysis_model_provider="custom:intranet", vision_analysis_model_name="vl")
    assert asyncio.run(describe_section_image(str(path), "deck.pptx", 2, 1)) == "图片页文字"
    assert calls[0][0] == "vision_analysis"
    assert calls[0][1].startswith("data:image/png;base64,")
