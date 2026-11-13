import json, sys
from docx import Document
files = {
    "exp1": r"D:\u+小助手\reports\experiment_reports\experiment_085b0061a25541d3a6ce276c614d9de0.docx",
    "exp2": r"D:\u+小助手\reports\experiment_reports\experiment_e72cd0d2206d4094bf61eacbb4d02a7c.docx",
}
for k, p in files.items():
    d = Document(p)
    lines = []
    for i, par in enumerate(d.paragraphs):
        t = par.text.strip()
        if t:
            lines.append(f"[{i}][{par.style.name}] {t}")
    out = "\n".join(lines)
    with open(rf"D:\u+小助手\reports\_dump_{k}.txt", "w", encoding="utf-8") as f:
        f.write(out)
    print(k, "paragraphs:", len(d.paragraphs), "chars:", len(out))
