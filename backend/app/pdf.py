from io import BytesIO
from textwrap import wrap

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


def make_pdf(title: str, lines: list[str]) -> bytes:
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4)
    canvas.setTitle(title)
    y = 790
    for index, line in enumerate([title, "", *lines]):
        for part in wrap(line, width=95) or [""]:
            if y < 55:
                canvas.showPage()
                y = 790
            canvas.setFont("Helvetica-Bold" if index == 0 else "Helvetica", 16 if index == 0 else 10)
            canvas.drawString(45, y, part)
            y -= 19 if index == 0 else 15
    canvas.save()
    return output.getvalue()
