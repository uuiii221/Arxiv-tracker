"""Find method figure captions and render original PDF graphics for each paper."""
from hashlib import sha256
import html
from io import BytesIO
from pathlib import Path
import re
import shutil


_CAPTION = re.compile(r"^\s*(?:Figure|Fig\.?)[\s\u00a0]*(\d+)\s*[:.\-]?\s+", re.I)
_METHOD = re.compile(r"\b(?:framework|pipeline|workflow|architecture|schematic|flowchart)\b|"
                     r"\b(?:overview|overall illustration)\s+of\b", re.I)
_RESULT = re.compile(r"\b(?:qualitative|quantitative|ablation|t-sne|segmentation results)\b", re.I)


def _score(caption):
    return len(_METHOD.findall(caption)) * 3 - len(_RESULT.findall(caption)) * 4


def _candidate_pages(text):
    candidates = []
    for page, body in re.findall(r"\[Page (\d+)\]\n(.*?)(?=\n\n\[Page \d+\]\n|\Z)", text, re.S):
        lines = body.splitlines()
        scores = [_score(" ".join(lines[index:index + 3]))
                  for index, line in enumerate(lines) if _CAPTION.match(line)]
        if scores and max(scores) > 0:
            candidates.append((max(scores), int(page)))
    return [page for score, page in sorted(candidates, key=lambda value: (-value[0], value[1]))[:6]]


def _captions(page):
    # Column crops avoid merging a left-column caption with unrelated right-column prose.
    views = [page, page.crop((0, 0, page.width / 2, page.height)),
             page.crop((page.width / 2, 0, page.width, page.height))]
    found = []
    for view in views:
        lines = view.extract_text_lines(return_chars=False)
        for index, line in enumerate(lines):
            match = _CAPTION.match(line["text"])
            if not match:
                continue
            caption_lines = [line]
            for following in lines[index + 1:index + 4]:
                if (following["top"] - caption_lines[-1]["bottom"] > 8
                        or _CAPTION.match(following["text"])):
                    break
                caption_lines.append(following)
            caption = " ".join(part["text"] for part in caption_lines)[:700]
            score = _score(caption)
            if score > 0:
                found.append({"number": int(match.group(1)), "caption": caption, "score": score,
                              "x0": min(part["x0"] for part in caption_lines),
                              "x1": max(part["x1"] for part in caption_lines),
                              "top": line["top"], "bottom": caption_lines[-1]["bottom"]})
    # Prefer the full caption when the number/position is duplicated by a column crop.
    unique = {}
    for caption in found:
        key = (caption["number"], round(caption["top"] / 3))
        old = unique.get(key)
        if old is None or caption["score"] > old["score"]:
            unique[key] = caption
    return list(unique.values())


def _figure_bbox(page, caption):
    """Crop only a nearby graphic group; retain the whole page if layout is unclear."""
    middle = page.width / 2
    if caption["x0"] >= middle:
        left, right = middle, page.width
    elif caption["x1"] <= middle:
        left, right = 0, middle
    else:
        left, right = 0, page.width
    graphics = [obj for obj in page.images + page.rects + page.curves + page.lines
                if obj["x0"] >= left - 4 and obj["x1"] <= right + 4
                and 20 < obj["top"] < obj["bottom"] <= caption["top"] + 2
                and obj["bottom"] - obj["top"] >= 3]
    graphics.sort(key=lambda obj: obj["top"])
    groups = []
    for obj in graphics:
        if not groups or obj["top"] - groups[-1][3] > 22:
            groups.append([obj["x0"], obj["top"], obj["x1"], obj["bottom"]])
        else:
            group = groups[-1]
            group[:] = [min(group[0], obj["x0"]), min(group[1], obj["top"]),
                        max(group[2], obj["x1"]), max(group[3], obj["bottom"])]
    if not groups:
        return None
    box = groups[-1]
    if caption["top"] - box[3] > 55 or box[3] - box[1] < 60 or box[2] - box[0] < 100:
        return None
    # Include text labels beside graphical objects, then include the original caption.
    nearby = [char for char in page.chars if left <= char["x0"] <= right
              and box[1] - 12 <= char["top"] <= box[3] + 8]
    xs = [box[0], caption["x0"]] + [char["x0"] for char in nearby]
    xe = [box[2], caption["x1"]] + [char["x1"] for char in nearby]
    return (max(0, min(xs) - 8), max(0, box[1] - 12),
            min(page.width, max(xe) + 8), min(page.height, caption["bottom"] + 8))


def extract_framework_figure(source, text, *, pdf_url, output_dir, config=None):
    """Return at most one PNG/provenance record; never substitute an unrelated figure."""
    import pdfplumber

    config = config or {}
    pages = _candidate_pages(text)
    if not pages:
        return None
    source.seek(0)
    with pdfplumber.open(source, pages=pages) as document:
        candidates = [(caption, page) for page in document.pages for caption in _captions(page)]
        if not candidates:
            return None
        caption, page = max(candidates, key=lambda pair: (pair[0]["score"], -pair[1].page_number))
        box = _figure_bbox(page, caption)
        rendered = page.crop(box) if box else page
        width = min(1800, max(600, int(config.get("width", 1400))))
        # A tall/narrow figure must not cause an unbounded rendering allocation.
        resolution = min(72 * width / rendered.width, 72 * 2400 / rendered.height, 180)
        image = rendered.to_image(resolution=resolution, antialias=True).original.convert("RGB")
        limit = min(2 * 1024 * 1024, max(32 * 1024, int(config.get("max_bytes", 1024 * 1024))))
        data = b""
        for _ in range(5):
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            data = buffer.getvalue()
            if len(data) <= limit:
                break
            image = image.resize((max(1, int(image.width * .8)), max(1, int(image.height * .8))))
        if len(data) > limit:
            raise ValueError("Original figure exceeds figures.max_bytes after resizing")
        digest = sha256(data).hexdigest()
        filename = f"{digest[:24]}.png"
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(data)
        return {"filename": filename, "number": caption["number"], "page": page.page_number,
                "caption": caption["caption"], "kind": "figure" if box else "page",
                "source_url": pdf_url + f"#page={page.page_number}", "sha256": digest,
                "width": image.width, "height": image.height, "bytes": len(data)}


def figure_filename(figure):
    filename = figure.get("filename") or ""
    if not re.fullmatch(r"[a-f0-9]{24}\.png", filename):
        raise ValueError("Invalid original figure filename")
    return filename


def figure_cid(figure):
    return figure_filename(figure)[:-4] + "@arxiv-tracker"


def local_figure_path(item, figure):
    directory = item.get("_figure_dir")
    if not directory:
        raise ValueError("Original figure directory is missing")
    path = Path(directory) / figure_filename(figure)
    if not path.is_file():
        raise ValueError("Original figure file is missing")
    if sha256(path.read_bytes()).hexdigest() != figure["sha256"]:
        raise ValueError("Original figure checksum does not match")
    return path


def copy_site_figures(items, site_dir):
    for item in items:
        for figure in item.get("figures") or []:
            destination = Path(site_dir) / "assets" / "figures" / figure_filename(figure)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_figure_path(item, figure), destination)


def render_figures_html(item, *, prefix=None):
    parts = []
    for figure in item.get("figures") or []:
        src = prefix + figure_filename(figure) if prefix is not None else "cid:" + figure_cid(figure)
        label = "原论文流程图" if figure["kind"] == "figure" else "原论文流程图所在页（完整页面）"
        label += f" · Figure {figure['number']} · PDF 第 {figure['page']} 页"
        parts.append('<div style="margin:12px 0"><p><b>' + html.escape(label) + '</b></p>'
                     '<img src="' + html.escape(src, quote=True) + '" alt="' + html.escape(label, quote=True)
                     + '" style="display:block;max-width:100%;height:auto" width="700">'
                     '<p style="font-size:12px;color:#667085">' + html.escape(figure["caption"])
                     + ' <a href="' + html.escape(figure["source_url"], quote=True) + '">原文来源</a></p></div>')
    if not parts and item.get("figure_status") in ("not_found", "error"):
        parts.append('<p>本篇未找到可可靠提取的方法流程图，请查看原文 PDF。</p>')
    return "\n".join(parts)


def render_figures_markdown(item):
    lines = []
    for figure in item.get("figures") or []:
        filename = figure_filename(figure)
        label = "原论文流程图" if figure["kind"] == "figure" else "原论文流程图所在页（完整页面）"
        lines.extend([f"#### {label} · Figure {figure['number']} · PDF 第 {figure['page']} 页", "",
                      f"![{label}](figures/{filename})", "", figure["caption"], "",
                      f"[原文来源]({figure['source_url']})", ""])
    if not lines and item.get("figure_status") in ("not_found", "error"):
        lines.extend(["本篇未找到可可靠提取的方法流程图，请查看原文 PDF。", ""])
    return lines
