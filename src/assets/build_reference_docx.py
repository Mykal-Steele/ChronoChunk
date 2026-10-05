"""Builds reference.docx, the Word template that gives the bot's DOCX files their look.

It starts from pandoc's default template and changes a few styles so a Word
file matches the PDFs: dark headings instead of blue, an 11pt body, the title
on the left, and tables with a grid and a shaded header row.

Run it again after changing the values below (pandoc must be installed):

    python src/assets/build_reference_docx.py
"""
import io
import re
import subprocess
import zipfile
from pathlib import Path

TEXT_COLOR = "1A1A1A"
LINK_COLOR = "0B57D0"
BORDER_COLOR = "B8B8B8"
HEADER_FILL = "EFEFEF"
FONT = "Calibri"

OUTPUT = Path(__file__).parent / "reference.docx"

TABLE_BORDERS = "<w:tblBorders>" + "".join(
    f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="{BORDER_COLOR}" />'
    for side in ("top", "left", "bottom", "right", "insideH", "insideV")
) + "</w:tblBorders>"


def restyle_one(style: str) -> str:
    """Apply the look to a single <w:style> element."""
    style_id = re.search(r'w:styleId="([^"]+)"', style).group(1)

    color = LINK_COLOR if style_id == "Hyperlink" else TEXT_COLOR
    style = re.sub(r'<w:color w:val="[0-9A-Fa-f]{6}"[^>]*/>', f'<w:color w:val="{color}" />', style)
    style = re.sub(r'<w:rFonts w:asciiTheme="[^"]+"[^>]*/>', f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" />', style)

    if style_id == "Title":
        style = style.replace('<w:jc w:val="center" />', "")

    if style_id == "Table":
        style = style.replace('<w:tblInd w:w="0" w:type="dxa" />', '<w:tblInd w:w="0" w:type="dxa" />' + TABLE_BORDERS, 1)
        style = style.replace('<w:top w:w="0" w:type="dxa" />', '<w:top w:w="40" w:type="dxa" />')
        style = style.replace('<w:bottom w:w="0" w:type="dxa" />', '<w:bottom w:w="40" w:type="dxa" />')
        # Header row: bold on a light fill
        style = style.replace('<w:tblStylePr w:type="firstRow">', '<w:tblStylePr w:type="firstRow"><w:rPr><w:b /></w:rPr>', 1)
        style = style.replace("</w:tcBorders>", f'</w:tcBorders><w:shd w:val="clear" w:color="auto" w:fill="{HEADER_FILL}" />', 1)
    return style


def restyle(styles: str) -> str:
    """Apply the look to the whole styles.xml."""
    # Default font and an 11pt body (sizes are in half points)
    styles = re.sub(r'<w:rFonts w:asciiTheme="minorHAnsi"[^>]*/>', f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cstheme="minorBidi" />', styles, count=1)
    styles = styles.replace('<w:sz w:val="24" />', '<w:sz w:val="22" />', 1).replace('<w:szCs w:val="24" />', '<w:szCs w:val="22" />', 1)
    return re.sub(r"<w:style .*?</w:style>", lambda m: restyle_one(m.group(0)), styles, flags=re.DOTALL)


def main() -> None:
    default = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"], capture_output=True, check=True
    ).stdout

    source = zipfile.ZipFile(io.BytesIO(default))
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/styles.xml":
                data = restyle(data.decode("utf-8")).encode("utf-8")
            target.writestr(item, data)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
