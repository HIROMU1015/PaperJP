"""Non-executing LaTeX AST extraction, with original file/line provenance."""
from __future__ import annotations

import re
import copy
from collections import Counter
from pathlib import Path

from pylatexenc.latex2text import LatexNodes2Text
from pylatexenc.latexwalker import LatexWalker
from pylatexenc.macrospec import MacroSpec, MacroStandardArgsParser

from .common import PipelineError, issue, safe_path

MATH_ENVS = {"equation", "equation*", "align", "align*", "eqnarray", "eqnarray*", "gather", "gather*", "multline", "multline*", "displaymath", "math"}
HEADINGS = {"section": 2, "subsection": 3, "subsubsection": 4, "paragraph": 5, "chapter": 2}
LAYOUT = {"maketitle", "centering", "small", "large", "Large", "normalsize", "noindent", "newpage", "clearpage", "pagestyle", "thispagestyle", "vspace", "hspace", "vskip", "hskip", "label", "bibliographystyle", "tableofcontents", "appendix", "protect", "relax"}


def strip_comments(text):
    # Keep offsets stable for provenance.
    return re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m[0]), text)


def decode(path):
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def expanded_source(root, main=None):
    root = Path(root).resolve()
    files = sorted(root.rglob("*.tex"))
    if main:
        selected = safe_path(root, main)
    else:
        candidates = [p for p in files if re.search(r"\\(?:documentclass|documentstyle)\b", strip_comments(decode(p)))]
        if len(candidates) != 1:
            raise PipelineError(f"Select the main TeX file using --main; candidates: {[p.relative_to(root).as_posix() for p in candidates]}")
        selected = candidates[0]
    segments, mapping, visiting, included = [], [], [], []
    length = 0

    def expand(path):
        nonlocal length
        path = path.resolve()
        if not path.is_relative_to(root) or path in visiting or len(visiting) > 30:
            raise PipelineError(f"Unsafe/cyclic TeX include: {path}")
        if not path.is_file():
            raise PipelineError(f"Missing TeX include: {path}")
        visiting.append(path)
        included.append(path.relative_to(root).as_posix())
        text = strip_comments(decode(path))

        def append(start, end):
            nonlocal length
            value = text[start:end]
            mapping.append({"start": length, "end": length + len(value), "file": path.relative_to(root).as_posix(), "source_offset": start, "line": text[:start].count("\n") + 1, "text": value})
            segments.append(value)
            length += len(value)
            if length > 10_000_000:
                raise PipelineError("Expanded TeX exceeds 10 million characters")

        cursor = 0
        pattern = r"\\(?:input|include)(?![A-Za-z])\s*(?:\{([^}]+)\}|([^\s{}]+))|\\bibliography\s*\{([^}]+)\}"
        for match in re.finditer(pattern, text):
            append(cursor, match.start())
            if match[3]:
                bbl = path.with_suffix(".bbl")
                if not bbl.exists():
                    available = list(root.rglob("*.bbl"))
                    if len(available) == 1:
                        bbl = available[0]
                if not bbl.exists():
                    raise PipelineError("Bibliography requires an existing .bbl; BibTeX is never executed")
                expand(bbl)
            else:
                name = (match[1] or match[2]).strip()
                if not Path(name).suffix:
                    name += ".tex"
                target = safe_path(path.parent, name)
                if not target.is_file():
                    target = safe_path(root, name)
                expand(target)
            cursor = match.end()
        append(cursor, len(text))
        visiting.pop()

    expand(selected)
    return "".join(segments), mapping, included


def parse_nodes(text):
    from pylatexenc.latexwalker import get_default_latex_context_db
    context = get_default_latex_context_db()
    specs = [MacroSpec(name, MacroStandardArgsParser(argspec=spec)) for name, spec in {
        "includegraphics": "*[{", "caption": "[{", "captionof": "{{", "label": "{", "ref": "{", "eqref": "{", "autoref": "{", "cref": "{", "Cref": "{", "cite": "*[[{", "citep": "*[[{", "citet": "*[[{", "footnote": "[{", "bibitem": "[{", "thanks": "{", "author": "{", "affiliation": "{", "date": "{", "keywords": "{", "url": "{", "href": "{{", "newcommand": "*{[[{", "renewcommand": "*{[[{", "providecommand": "*{[[{", "DeclareMathOperator": "*{{", "graphicspath": "{", "setcounter": "{{", "addtocounter": "{{", "setlength": "{{", "bibliographystyle": "{", "vspace": "*{", "hspace": "*{", "thispagestyle": "{", "pagestyle": "{", "textcolor": "{{"
    }.items()]
    context.add_context_category("paper-translator", macros=specs, prepend=True)
    # TeX register assignments control layout, not paper content. Preserve offsets.
    text = re.sub(r"\\(?:baselineskip|parindent|parskip)\s*=\s*[-+.\d]+\s*(?:pt|cm|mm|in|em|ex)", lambda m: " " * len(m[0]), text)
    nodes, _, _ = LatexWalker(text, latex_context=context, tolerant_parsing=False).get_latex_nodes()
    return nodes


def arguments(node):
    return [n for n in getattr(getattr(node, "nodeargd", None), "argnlist", []) if n is not None]


def inner(node):
    return getattr(node, "nodelist", [])


def raw(node):
    return node.latex_verbatim()


def group_text(node):
    value = raw(node)
    return value[1:-1] if value.startswith(("{", "[")) else value


def plain(text):
    return LatexNodes2Text().latex_to_text(text).strip()


def extract_tex(root, main=None):
    text, mapping, included = expanded_source(root, main)
    nodes = parse_nodes(text)
    blocks, problems, counters = [], [], Counter()
    section, appendix, bibliography = 0, False, False
    labels = {}
    pending = []
    inline_records = {}
    math_counter = 0
    defined_macros = set(re.findall(r"\\(?:newcommand|renewcommand|providecommand|DeclareMathOperator)\*?\s*\{?\\([A-Za-z]+)", text))

    def audit_math(value, pos):
        unresolved = defined_macros & set(re.findall(r"\\([A-Za-z]+)", value))
        if unresolved:
            problems.append(issue("custom_math_macro", f"Math uses unexpanded document-defined macros {sorted(unresolved)} at {origin(pos)}; renderer compatibility is unverified"))

    def origin(pos, size=0):
        segments = [seg for seg in mapping if seg["start"] <= pos < seg["end"]]
        if not segments:
            return {"file": included[0], "line": 1, "expanded_offset": pos}
        seg = segments[0]
        offset = pos - seg["start"]
        return {"file": seg["file"], "line": seg["line"] + seg["text"][:offset].count("\n"), "offset": seg["source_offset"] + offset, "expanded_offset": pos, "length": size}

    def emit(kind, value, pos=0, **extra):
        value = value.strip()
        if not value:
            return
        key = (section, kind)
        counters[key] += 1
        short = {"paragraph": "p", "heading": "h", "equation": "eq", "figure": "fig", "caption": "cap", "table": "tab", "reference": "bib", "footnote": "fn", "title": "title", "metadata": "meta"}.get(kind, kind)
        block_id = f"sec{section:02d}-{short}{counters[key]:03d}"
        blocks.append({"id": block_id, "kind": kind, "section": section, "appendix": appendix, "text": value, "translate": kind not in {"equation", "figure", "reference", "metadata"}, "source": origin(pos), **extra})
        return block_id

    def inlines(ns, reference=False):
        parts = []
        for node in ns:
            t = type(node).__name__
            if t == "LatexCharsNode":
                parts.append(node.chars.replace("~", " ").replace("---", "—").replace("--", "–").replace("``", '“').replace("''", '”'))
            elif t == "LatexCommentNode":
                continue
            elif t == "LatexGroupNode":
                parts.append(inlines(inner(node), reference))
            elif t == "LatexMathNode":
                original_math = raw(node)
                value = original_math
                audit_math(value, node.pos)
                if node.displaytype == "inline":
                    content = value[len(node.delimiters[0]):-len(node.delimiters[1])]
                    # Markdown inline math rejects edge whitespace and source line wraps.
                    # Retain the exact original separately; preserve all non-whitespace TeX.
                    value = "$" + content.strip().replace("\n", " ") + "$"
                    inline_records[node.pos] = {"raw_tex": original_math, "markdown": value, "source": origin(node.pos, node.len)}
                parts.append(value)
            elif t == "LatexMacroNode":
                name, args = node.macroname, arguments(node)
                if name in {"emph", "textit", "textbf", "textrm", "texttt", "textsf", "mbox", "textnormal", "underline"} and args:
                    value = inlines(inner(args[-1]), reference)
                    parts.append(("**" + value + "**") if name == "textbf" else ("*" + value + "*") if name in {"emph", "textit"} else value)
                elif name in {"bf", "it", "em", "rm", "sc", "tt", "sl", "protect", "relax", "newblock", "unskip"}:
                    pass
                elif name.startswith("cite") and args:
                    parts.extend(f"[cite:{key.strip()}](#ref-{key.strip()})" for key in group_text(args[-1]).split(","))
                elif name in {"ref", "eqref", "autoref", "cref", "Cref"} and args:
                    key = group_text(args[-1])
                    parts.append(f"[ref:{key}](#label-{key})")
                elif name == "label" and args:
                    key = group_text(args[-1])
                    parts.append(f'<a id="label-{key}"></a>')
                    labels[key] = True
                elif name == "footnote" and args:
                    key = f"fn{len(pending) + 1 + sum(b['kind'] == 'footnote' for b in blocks)}"
                    pending.append((key, inlines(inner(args[-1])), node.pos))
                    number = key.removeprefix("fn")
                    parts.append(f'<a id="fnref-{key}"></a><sup>[{number}](#footnote-{key})</sup>')
                elif name in {"url", "href"} and args:
                    url = group_text(args[0])
                    parts.append(f"[{inlines(inner(args[-1])) if name == 'href' else url}]({url})")
                elif name in {"\\", "newline", "linebreak"}:
                    parts.append("\n")
                elif name in {"&", "%", "_", "#", "$", "{", "}"}:
                    parts.append(name if name != "$" else "\\$")
                elif name in {",", ";", " ", "quad", "qquad"}:
                    parts.append(" ")
                elif name == "textcolor" and args:
                    parts.append(inlines(inner(args[-1])))
                elif name in LAYOUT:
                    pass
                else:
                    converted = plain(raw(node))
                    if converted and converted != raw(node) and (args or name in {"LaTeX", "TeX", "ldots", "dots", "textendash", "textemdash"}):
                        parts.append(converted)
                    else:
                        problems.append(issue("unknown_macro", f"Unsupported prose macro \\{name} at {origin(node.pos)}"))
                        parts.append(raw(node))
            elif t == "LatexSpecialsNode":
                parts.append(" " if raw(node) == "~" else raw(node))
            else:
                problems.append(issue("unknown_inline", f"Unsupported {t}: {raw(node)[:100]}"))
                parts.append(raw(node))
        return "".join(parts)

    def flush(buffer):
        if not buffer:
            return
        value = inlines(buffer)
        # Whitespace normalization must never rewrite inline mathematics.
        from .protect import protect
        masked, protected = protect(value, f"flush-{buffer[0].pos}")
        for paragraph in re.split(r"\n\s*\n", masked):
            paragraph = re.sub(r"[ \t]*\n[ \t]*", " ", paragraph)
            for token, original in protected.items():
                paragraph = paragraph.replace(token, original)
            emit("paragraph", paragraph, buffer[0].pos)
        buffer.clear()
        while pending:
            key, value, pos = pending.pop(0)
            emit("footnote", f'<a id="footnote-{key}"></a>\n{key.removeprefix("fn")}. {value} [↩](#fnref-{key})', pos)

    def walk(ns):
        nonlocal section, appendix, bibliography, math_counter
        buffer = []
        for node in ns:
            t = type(node).__name__
            name = getattr(node, "macroname", None)
            env = getattr(node, "environmentname", None)
            args = arguments(node)
            if t == "LatexCharsNode" and re.search(r"\n\s*\n", node.chars):
                cursor = 0
                for separator in re.finditer(r"\n\s*\n", node.chars):
                    fragment = copy.copy(node)
                    fragment.pos, fragment.len, fragment.chars = node.pos + cursor, separator.start() - cursor, node.chars[cursor:separator.start()]
                    if fragment.chars:
                        buffer.append(fragment)
                    flush(buffer)
                    cursor = separator.end()
                fragment = copy.copy(node)
                fragment.pos, fragment.len, fragment.chars = node.pos + cursor, len(node.chars) - cursor, node.chars[cursor:]
                if fragment.chars:
                    buffer.append(fragment)
            elif env == "document":
                flush(buffer)
                walk(inner(node))
            elif name in HEADINGS:
                flush(buffer)
                if name in {"section", "chapter"}:
                    section += 1
                emit("heading", "#" * HEADINGS[name] + " " + inlines(inner(args[-1])), node.pos)
            elif name == "title":
                flush(buffer)
                emit("title", "# " + inlines(inner(args[-1])), node.pos)
            elif name in {"author", "date", "affiliation", "thanks", "keywords"}:
                flush(buffer)
                if args:
                    emit("metadata", inlines(inner(args[-1])), node.pos)
            elif name == "appendix":
                flush(buffer)
                appendix = True
            elif env == "abstract":
                flush(buffer)
                emit("heading", "## Abstract", node.pos, synthetic=True)
                walk(inner(node))
            elif env in MATH_ENVS or (t == "LatexMathNode" and node.displaytype == "display"):
                flush(buffer)
                math_counter += 1
                original = raw(node)
                audit_math(original, node.pos)
                expression = text[node.pos:node.pos + node.len]
                if env:
                    expression = re.sub(r"^\\begin\{[^}]+\}|\\end\{[^}]+\}$", "", expression).strip()
                    if env.startswith(("align", "eqnarray")):
                        expression = "\\begin{aligned}\n" + expression + "\n\\end{aligned}"
                else:
                    expression = expression[len(node.delimiters[0]):-len(node.delimiters[1])].strip()
                keys = re.findall(r"\\label\{([^}]+)\}", expression)
                expression = re.sub(r"\\label\{[^}]+\}", "", expression)
                anchors = "\n".join(f'<a id="label-{key}"></a>' for key in keys)
                labels.update({key: True for key in keys})
                emit("equation", (anchors + "\n" if anchors else "") + "$$\n" + expression + "\n$$", node.pos, raw_tex=original, labels=keys)
            elif env in {"figure", "figure*", "table", "table*"}:
                flush(buffer)
                for child in inner(node):
                    if getattr(child, "environmentname", "") in {"tabular", "tabular*", "tabularx"}:
                        table(child)
                    elif getattr(child, "macroname", "") == "includegraphics":
                        image(child)
                    elif getattr(child, "macroname", "") == "caption":
                        emit("caption", inlines(inner(arguments(child)[-1])), child.pos)
                    elif getattr(child, "macroname", "") == "label":
                        emit("anchor", inlines([child]), child.pos, translate=False)
                    elif type(child).__name__ == "LatexGroupNode":
                        walk(inner(child))
                    elif type(child).__name__ == "LatexCharsNode" and not child.chars.strip():
                        pass
                    elif getattr(child, "macroname", "") in LAYOUT:
                        pass
                    else:
                        walk([child])
            elif name == "includegraphics":
                flush(buffer)
                image(node)
            elif env in {"tabular", "tabular*", "tabularx"}:
                flush(buffer)
                table(node)
            elif env == "thebibliography":
                flush(buffer)
                bibliography = True
                emit("heading", "## References", node.pos, synthetic=True)
                entries = []
                current_key, current = None, []
                for child in inner(node):
                    if getattr(child, "macroname", "") == "bibitem":
                        if current_key:
                            entries.append((current_key, current))
                        current_key, current = group_text(arguments(child)[-1]), []
                    else:
                        current.append(child)
                if current_key:
                    entries.append((current_key, current))
                for key, content in entries:
                    emit("reference", f'<a id="ref-{key}"></a>\n' + inlines(content, True).strip(), content[0].pos if content else node.pos, citation_key=key)
                bibliography = False
            elif env in {"itemize", "enumerate", "description"}:
                flush(buffer)
                children, item_buffer = inner(node), []
                item_index = 0
                for child in children:
                    if getattr(child, "macroname", "") == "item":
                        if item_buffer:
                            emit("paragraph", (f"{item_index}. " if env == "enumerate" else "- ") + inlines(item_buffer), item_buffer[0].pos)
                        item_index += 1
                        item_buffer = []
                    else:
                        item_buffer.append(child)
                if item_buffer:
                    emit("paragraph", (f"{item_index}. " if env == "enumerate" else "- ") + inlines(item_buffer), node.pos)
            elif env in {"center", "flushleft", "flushright", "quote", "quotation"}:
                flush(buffer)
                walk(inner(node))
            elif env:
                flush(buffer)
                problems.append(issue("unknown_environment", f"Unsupported environment {env} at {origin(node.pos)}"))
                emit("paragraph", raw(node), node.pos)
            elif name in LAYOUT or name in {"documentclass", "documentstyle", "usepackage", "newcommand", "renewcommand", "providecommand", "DeclareMathOperator", "graphicspath", "setlength", "setcounter", "addtocounter"}:
                flush(buffer)
            else:
                buffer.append(node)
        flush(buffer)

    def image(node):
        args = arguments(node)
        name = group_text(args[-1])
        emit("figure", f"![figure]({name})", node.pos, asset_source=name)

    def table(node):
        # Parse cell syntax before converting inline content; do not split inside math/groups.
        rows, row, cell = [], [], []
        for child in inner(node):
            if getattr(child, "macroname", "") in {"hline", "toprule", "midrule", "bottomrule", "cline"}:
                continue
            if getattr(child, "macroname", "") in {"\\", "tabularnewline"}:
                row.append(inlines(cell).strip())
                rows.append(row)
                row, cell = [], []
            elif type(child).__name__ == "LatexSpecialsNode" and raw(child) == "&":
                row.append(inlines(cell).strip())
                cell = []
            else:
                cell.append(child)
        if cell and inlines(cell).strip():
            row.append(inlines(cell).strip())
        if row:
            rows.append(row)
        if not rows or len({len(r) for r in rows}) > 1:
            problems.append(issue("complex_table", f"Unsupported/ragged table at {origin(node.pos)}"))
            emit("table", raw(node), node.pos)
            return
        table_rows = ["| " + " | ".join(c.replace("|", "&#124;").replace("\n", " ") for c in r) + " |" for r in rows]
        table_rows.insert(1, "| " + " | ".join("---" for _ in rows[0]) + " |")
        emit("table", "\n".join(table_rows), node.pos, raw_tex=raw(node))

    # Ignore executable/layout preamble, but retain title/author/date metadata.
    for node in nodes:
        if getattr(node, "macroname", "") in {"title", "author", "date", "affiliation"} or getattr(node, "environmentname", "") == "document":
            walk([node])
    if not any(getattr(n, "environmentname", "") == "document" for n in nodes):
        raise PipelineError("Only LaTeX documents are currently supported (plain TeX is diagnostic-only)")
    # Older articles sometimes express title/abstract and bibliography using layout
    # rather than semantic macros. Recognize only explicit, conservative patterns.
    if not any(b["kind"] == "title" for b in blocks):
        first = next((b for b in blocks if b["kind"] == "paragraph"), None)
        if first and re.search(r"\\begin\{center\}\s*\{\\Large", text):
            first["kind"], first["text"] = "title", "# " + first["text"]
            index = blocks.index(first)
            if len(blocks) > index + 1:
                blocks[index + 1]["kind"] = "metadata"
                blocks[index + 1]["translate"] = False
    for block in blocks:
        if block["text"].startswith("Abstract:"):
            block["text"] = "## Abstract\n\n" + block["text"][len("Abstract:"):].strip()
        if block["kind"] == "paragraph" and re.match(r"^\([1-9]\d*\)\s+", block["text"]) and len(block["text"]) < 500:
            block["kind"], block["translate"] = "reference", False
            block["manual_bibliography"] = True
    # Keep machine citation keys in anchor targets; show readable source numbering.
    citation_numbers = {b["citation_key"]: str(i) for i, b in enumerate((b for b in blocks if b.get("citation_key")), 1)}
    label_numbers = {}
    numbers = Counter()
    current_number = ""
    for block in blocks:
        if block["kind"] in {"equation", "figure", "table"}:
            numbers[block["kind"]] += 1
            current_number = str(numbers[block["kind"]])
        elif block["kind"] == "heading":
            current_number = str(block["section"])
        for key in re.findall(r'<a id="label-([^"]+)"', block["text"]):
            label_numbers[key] = current_number
    for block in blocks:
        block["text"] = re.sub(r"\[cite:([^\]]+)\]\(#ref-([^\)]+)\)", lambda m: f"[{citation_numbers.get(m[1], m[1])}](#ref-{m[2]})", block["text"])
        block["text"] = re.sub(r"\[ref:([^\]]+)\]\(#label-([^\)]+)\)", lambda m: f"[{label_numbers.get(m[1], m[1])}](#label-{m[2]})", block["text"])
    for index, (position, record) in enumerate(sorted(inline_records.items()), 1):
        candidates = [b for b in blocks if b["source"]["expanded_offset"] <= position and record["markdown"] in b["text"]]
        if candidates:
            owner = max(candidates, key=lambda b: b["source"]["expanded_offset"])
            owner.setdefault("inline_math", []).append({"id": f"{owner['id']}-m{index:04d}", **record})
    return blocks, {"issues": problems, "included_files": included, "expanded_tex": text, "mapping": [{k: v for k, v in x.items() if k != "text"} for x in mapping]}
