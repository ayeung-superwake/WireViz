# -*- coding: utf-8 -*-

import base64
import re
from pathlib import Path
from typing import Callable, Dict, List, Union

import wireviz  # for doing wireviz.__file__
from wireviz import APP_NAME, APP_URL, __version__
from wireviz.wv_dataclasses import Metadata, Options
from wireviz.wv_utils import (
    file_read_text,
    file_write_text,
    html_line_breaks,
    smart_file_resolve,
)

mime_subtype_replacements = {"jpg": "jpeg", "tif": "tiff"}


# TODO: Share cache and code between data_URI_base64() and embed_svg_images()
def data_URI_base64(file: Union[str, Path], media: str = "image") -> str:
    """Return Base64-encoded data URI of input file."""
    file = Path(file)
    b64 = base64.b64encode(file.read_bytes()).decode("utf-8")
    uri = f"data:{media}/{get_mime_subtype(file)};base64, {b64}"
    # print(f"data_URI_base64('{file}', '{media}') -> {len(uri)}-character URI")
    if len(uri) > 65535:
        print(
            "data_URI_base64(): Warning: Browsers might have different URI length limitations"
        )
    return uri


def embed_svg_images(svg_in: str, base_path: Union[str, Path] = Path.cwd()) -> str:
    images_b64 = {}  # cache of base64-encoded images

    def image_tag(pre: str, url: str, post: str) -> str:
        return f'<image{pre} xlink:href="{url}"{post}>'

    def replace(match: re.Match) -> str:
        imgurl = match["URL"]
        if not imgurl in images_b64:  # only encode/cache every unique URL once
            imgurl_abs = (Path(base_path) / imgurl).resolve()
            image = imgurl_abs.read_bytes()
            images_b64[imgurl] = base64.b64encode(image).decode("utf-8")
        return image_tag(
            match["PRE"] or "",
            f"data:image/{get_mime_subtype(imgurl)};base64, {images_b64[imgurl]}",
            match["POST"] or "",
        )

    pattern = re.compile(
        image_tag(r"(?P<PRE> [^>]*?)?", r'(?P<URL>[^"]*?)', r"(?P<POST> [^>]*?)?"),
        re.IGNORECASE,
    )
    return pattern.sub(replace, svg_in)


def get_mime_subtype(filename: Union[str, Path]) -> str:
    mime_subtype = Path(filename).suffix.lstrip(".").lower()
    if mime_subtype in mime_subtype_replacements:
        mime_subtype = mime_subtype_replacements[mime_subtype]
    return mime_subtype


def embed_svg_images_file(
    filename_in: Union[str, Path], overwrite: bool = True
) -> None:
    filename_in = Path(filename_in).resolve()
    filename_out = filename_in.with_suffix(".b64.svg")
    filename_out.write_text(  # TODO?: Verify xml encoding="utf-8" in SVG?
        embed_svg_images(filename_in.read_text(), filename_in.parent)
    )  # TODO: Use encoding="utf-8" in both read_text() and write_text()
    if overwrite:
        filename_out.replace(filename_in)


def generate_html_output(
    filename: Union[str, Path],
    bom: List[List[str]],
    metadata: Metadata,
    options: Options,
):
    # load HTML template
    templatename = metadata.get("template", {}).get("name")
    if templatename:
        # if relative path to template was provided,
        # check directory of YAML file first, fall back to built-in template directory
        templatefile = smart_file_resolve(
            f"{templatename}.html",
            [Path(filename).parent, Path(__file__).parent / "templates"],
        )
    else:
        # fall back to built-in simple template if no template was provided
        templatefile = Path(wireviz.__file__).parent / "templates/simple.html"

    html = file_read_text(templatefile)  # TODO?: Warn if unexpected meta charset?

    # embed SVG diagram (only if used)
    def svgdata() -> str:
        return re.sub(  # TODO?: Verify xml encoding="utf-8" in SVG?
            "^<[?]xml [^?>]*[?]>[^<]*<!DOCTYPE [^>]*>",
            "<!-- XML and DOCTYPE declarations from SVG file removed -->",
            file_read_text(f"{filename}.tmp.svg"),
            1,
        )

    # generate BOM table
    # generate BOM header (may be at the top or bottom of the table)
    bom_header_html = "  <tr>\n"
    for item in bom[0]:
        th_class = f"bom_col_{item.lower()}"
        bom_header_html = f'{bom_header_html}    <th class="{th_class}">{item}</th>\n'
    bom_header_html = f"{bom_header_html}  </tr>\n"

    # generate BOM contents
    bom_contents = []
    for row in bom[1:]:
        row_html = "  <tr>\n"
        for i, item in enumerate(row):
            td_class = f"bom_col_{bom[0][i].lower()}"
            row_html = f'{row_html}    <td class="{td_class}">{item if item is not None else ""}</td>\n'
        row_html = f"{row_html}  </tr>\n"
        bom_contents.append(row_html)

    bom_html = (
        '<table class="bom">\n' + bom_header_html + "".join(bom_contents) + "</table>\n"
    )
    bom_html_reversed = (
        '<table class="bom">\n'
        + "".join(list(reversed(bom_contents)))
        + bom_header_html
        + "</table>\n"
    )

    # prepare simple replacements
    replacements = {
        "<!-- %generator% -->": f"{APP_NAME} {__version__} - {APP_URL}",
        "<!-- %fontname% -->": options.fontname,
        "<!-- %bgcolor% -->": options.bgcolor.html,
        "<!-- %filename% -->": str(filename),
        "<!-- %filename_stem% -->": Path(filename).stem,
        "<!-- %bom% -->": bom_html,
        "<!-- %bom_reversed% -->": bom_html_reversed,
        "<!-- %sheet_current% -->": "1",  # TODO: handle multi-page documents
        "<!-- %sheet_total% -->": "1",  # TODO: handle multi-page documents
        "<!-- %template_sheetsize% -->": metadata.get("template", {}).get(
            "sheetsize", ""
        ),
    }

    def replacement_if_used(key: str, func: Callable[[], str]) -> None:
        """Append replacement only if used in html."""
        if key in html:
            replacements[key] = func()

    replacement_if_used("<!-- %diagram% -->", svgdata)
    replacement_if_used(
        "<!-- %diagram_png_b64% -->", lambda: data_URI_base64(f"{filename}.png")
    )

    # prepare metadata replacements
    if metadata:
        for item, contents in metadata.items():
            if isinstance(contents, (str, int, float)):
                replacements[f"<!-- %{item}% -->"] = html_line_breaks(str(contents))
            elif isinstance(contents, Dict):  # useful for authors, revisions
                for index, (category, entry) in enumerate(contents.items()):
                    if isinstance(entry, Dict):
                        replacements[f"<!-- %{item}_{index+1}% -->"] = str(category)
                        for entry_key, entry_value in entry.items():
                            replacements[
                                f"<!-- %{item}_{index+1}_{entry_key}% -->"
                            ] = html_line_breaks(str(entry_value))
                    elif isinstance(entry, (str, int, float)):
                        pass  # TODO?: replacements[f"<!-- %{item}_{category}% -->"] = html_line_breaks(str(entry))

    # data-driven revision rows for the title block (newest first) so the table has
    # exactly one row per revision — no empty hardcoded slots leaving broken cells.
    rev_rows = []
    if metadata and isinstance(metadata.get("revisions"), dict):
        for key, entry in reversed(list(metadata["revisions"].items())):
            e = entry if isinstance(entry, dict) else {}
            rev_rows.append(
                f'<div class="c">{html_line_breaks(str(key))}</div>'
                f'<div class="c wrap">{html_line_breaks(str(e.get("changelog", "")))}</div>'
                f'<div class="c">{html_line_breaks(str(e.get("date", "")))}</div>'
                f'<div class="c">{html_line_breaks(str(e.get("name", "")))}</div>'
            )
    replacements["<!-- %revisions_rows% -->"] = "".join(rev_rows)

    # latest revision key, for the title block's REV box (revisions authored newest-last)
    rev_current = ""
    if metadata and isinstance(metadata.get("revisions"), dict) and metadata["revisions"]:
        rev_current = html_line_breaks(str(list(metadata["revisions"].keys())[-1]))
    replacements["<!-- %rev_current% -->"] = rev_current

    # render info blocks (authored or model-derived) as a stack of headed blocks.
    # metadata["infoblocks"] is a list of {"heading": str, "rows": [[cell, ...], ...]};
    # a block with "kind": "labels" draws each row as a to-shape physical-label box
    # (cells -> stacked text lines) instead of a table row, so the label's outline and
    # content layout are conveyed. Otherwise the rows render as an HTML table.
    blocks_html = []
    if metadata and metadata.get("infoblocks"):
        for block in metadata["infoblocks"]:
            heading = html_line_breaks(str(block.get("heading", "")))
            if block.get("kind") == "labels":
                boxes = []
                for row in block.get("rows", []):
                    lines = "".join(
                        f'<div class="lbl-l">{html_line_breaks(str(cell))}</div>'
                        for cell in row
                    )
                    boxes.append(f'<div class="lbl">{lines}</div>')
                blocks_html.append(
                    f'<div class="ib"><div class="ib-h">{heading}</div>'
                    f'<div class="ib-labels">{"".join(boxes)}</div></div>'
                )
                continue
            body = []
            for row in block.get("rows", []):
                cells = "".join(
                    f"<td>{html_line_breaks(str(cell))}</td>" for cell in row
                )
                body.append(f"<tr>{cells}</tr>")
            blocks_html.append(
                f'<div class="ib"><div class="ib-h">{heading}</div>'
                f'<table class="ib-t">{"".join(body)}</table></div>'
            )
    # always set, so the slot is truly empty (not a leftover comment) when there
    # are no blocks — lets the template's :empty rule collapse the info pane.
    replacements["<!-- %infoblocks% -->"] = "".join(blocks_html)

    # DRAFT flag: default to empty so the data-draft attribute is clean when a
    # harness doesn't set it (the watermark only shows when it is "True").
    replacements.setdefault("<!-- %draft% -->", "")

    # Sheet-size label (standard designation only, no numeric dimensions) for the
    # title block. Dims (sheet_w/h) are still computed below — the zone frame needs them.
    sheet_sizes = {
        "ansi-a": ("ANSI A", 279.4, 215.9), "ansi-b": ("ANSI B", 431.8, 279.4),
        "ansi-c": ("ANSI C", 558.8, 431.8), "ansi-d": ("ANSI D", 863.6, 558.8),
        "ansi-e": ("ANSI E", 1117.6, 863.6),
        "iso-a4": ("ISO A4", 297, 210), "iso-a3": ("ISO A3", 420, 297),
        "iso-a2": ("ISO A2", 594, 420), "iso-a1": ("ISO A1", 841, 594),
        "iso-a0": ("ISO A0", 1189, 841), "letter": ("Letter", 279.4, 215.9),
        "legal": ("Legal", 355.6, 215.9), "tabloid": ("Tabloid", 431.8, 279.4),
        "a4": ("ISO A4", 297, 210), "a3": ("ISO A3", 420, 297), "a2": ("ISO A2", 594, 420),
    }
    tokens = str(metadata.get("template", {}).get("sheetsize", "")).split() if metadata else []
    portrait = "portrait" in tokens
    sheet_label, sheet_w, sheet_h = "", None, None
    for tok in tokens:
        if tok in sheet_sizes:
            name, w, h = sheet_sizes[tok]
            if portrait:
                w, h = h, w
            sheet_label = name
            sheet_w, sheet_h = w, h
            break
    replacements["<!-- %sheetsize_label% -->"] = sheet_label

    # Zone-reference frame (ASME Y14.1 style): numbers across top+bottom, letters down
    # both sides, divisions sized to the sheet (~50 mm each). Built here (not JS) so it
    # prints and shows in every viewer. Emitted as grid STRIPS placed in the frame's
    # margin cells — even cells divide the span so numbers/letters land at the zone
    # centres and cell borders draw the boundary ticks. No edge-pinned absolute
    # positioning (which WebKit/QuickLook drop near the sheet edge).
    zone_html = ""
    if sheet_w:
        margin = 6.0
        cols = max(1, round((sheet_w - 2 * margin) / 50))
        rows = max(1, min(26, round((sheet_h - 2 * margin) / 50)))
        nums = "".join(f"<span>{i + 1}</span>" for i in range(cols))
        lets = "".join(f"<span>{chr(65 + j)}</span>" for j in range(rows))
        colcss = f"grid-template-columns:repeat({cols},1fr)"
        rowcss = f"grid-template-rows:repeat({rows},1fr)"
        zone_html = (
            f'<div class="zrow top" style="{colcss}">{nums}</div>'
            f'<div class="zrow bot" style="{colcss}">{nums}</div>'
            f'<div class="zcol left" style="{rowcss}">{lets}</div>'
            f'<div class="zcol right" style="{rowcss}">{lets}</div>'
        )
    replacements["<!-- %zoneframe% -->"] = zone_html

    # perform replacements
    # regex replacement adapted from:
    # https://gist.github.com/bgusach/a967e0587d6e01e889fd1d776c5f3729

    # longer replacements first, just in case
    replacements_sorted = sorted(replacements, key=len, reverse=True)
    replacements_escaped = map(re.escape, replacements_sorted)
    pattern = re.compile("|".join(replacements_escaped))
    html = pattern.sub(lambda match: replacements[match.group(0)], html)

    file_write_text(f"{filename}.html", html)
