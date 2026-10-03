"""Draws STL networks as ladder rungs, as text or as an HTML page with SVG graphics.

STEP 7 has no ladder export, so this rebuilds rungs from the STL of a block:
contacts (A/AN/O/ON, nested brackets), edges, NOT, compares, coils (=, S, R), timers,
counters, and boxes for moves, arithmetic and block calls (including the EN pattern
"logic; JNB label; ...; label: NOP 0"). Networks that use anything else stay as STL.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# English and German (U/UN, ZV/ZR, SI/SV/SA) mnemonics
AND_OPS = {"A": False, "AN": True, "U": False, "UN": True}
OR_OPS = {"O": False, "ON": True}
BIT_OPS = {**AND_OPS, **OR_OPS}
COILS = {"=": "", "S": "S ", "R": "R "}
TIMER_COUNTER_OPS = {"SD", "SE", "SP", "SS", "SF", "SI", "SV", "SA", "CU", "CD", "ZV", "ZR", "FR"}
EDGE_OPS = {"FP": "P", "FN": "N"}
IGNORED = {"NOP", "BLD"}
EN_JUMPS = {"JNB", "JCN", "SPBNB", "SPBN"}
COMPARE = re.compile(r"^(==|<>|>=|<=|>|<)[IDR]$")
ARITHMETIC = re.compile(r"^([+\-*/][IDR]|MOD)$")


class NotLadder(Exception):
    pass


@dataclass
class Node:
    kind: str                      # no | nc | box | series | parallel
    label: str = ""
    children: list["Node"] = field(default_factory=list)


@dataclass
class Output:
    kind: str                      # coil | box
    label: str


@dataclass
class Rung:
    logic: Node | None
    outputs: list[Output]


@dataclass
class Network:
    number: int
    title: str
    comments: list[str]
    rungs: list[Rung] | None       # None: not drawable, see stl
    stl: list[str]


def _series(*nodes: Node | None) -> Node | None:
    parts: list[Node] = []
    for n in nodes:
        if n is not None:
            parts += n.children if n.kind == "series" else [n]
    return parts[0] if len(parts) == 1 else Node("series", children=parts) if parts else None


def _parallel(*nodes: Node | None) -> Node | None:
    parts: list[Node] = []
    for n in nodes:
        if n is not None:
            parts += n.children if n.kind == "parallel" else [n]
    return parts[0] if len(parts) == 1 else Node("parallel", children=parts) if parts else None


class _Parser:
    def __init__(self) -> None:
        self.rungs: list[Rung] = []
        self.terms: list[Node | None] = []     # OR-terms closed by a standalone "O"
        self.cur: Node | None = None           # the AND-chain being built
        self.stack: list[tuple[list[Node | None], Node | None, str, bool]] = []
        self.outputs: list[Output] = []
        self.loads: list[str] = []             # accumulator contents from L statements
        self.loads_used = False
        self.pending_label: str | None = None

    def _total(self) -> Node | None:
        return _parallel(*self.terms, self.cur)

    def _start_logic(self) -> None:
        if self.outputs:                        # logic after an output starts a new rung
            self.rungs.append(Rung(self._total(), self.outputs))
            self.terms, self.cur, self.outputs = [], None, []

    def _and_with(self, node: Node) -> None:
        self.cur = _series(self.cur, node)

    def _or_with(self, node: Node) -> None:
        self.cur, self.terms = _parallel(self._total(), node), []

    def _take_loads(self, count: int) -> list[str]:
        if len(self.loads) != count or self.loads_used:
            raise NotLadder
        taken, self.loads = self.loads, []
        return taken

    def feed(self, label: str | None, op: str, operand: str) -> None:
        if label is not None:
            if label != self.pending_label:
                raise NotLadder                 # a jump target we did not open
            self.pending_label = None
        if op in IGNORED or not op:
            return
        if op == "L":
            if self.loads_used:
                self.loads, self.loads_used = [], False
            if len(self.loads) >= 2:
                raise NotLadder
            self.loads.append(operand)
        elif op == "T":
            if len(self.loads) != 1:
                raise NotLadder
            self.outputs.append(Output("box", f"{self.loads[0]} -> {operand}"))
            self.loads_used = True              # "L a; T b; T c" moves the same value twice
        elif ARITHMETIC.match(op):
            a, b = self._take_loads(2)
            self.loads = [f"{a} {op} {b}"]
        elif COMPARE.match(op):
            a, b = self._take_loads(2)
            self._start_logic()
            self._and_with(Node("box", f"{a} {op} {b}"))
        elif op in TIMER_COUNTER_OPS:
            preset = f"  {self.loads[0]}" if len(self.loads) == 1 and not self.loads_used else ""
            self.loads = []
            self.outputs.append(Output("box", f"{op} {operand}{preset}"))
        elif op == "CALL":
            self.outputs.append(Output("box", f"CALL {operand}"))
        elif op in EN_JUMPS:
            if self.pending_label is not None or not operand:
                raise NotLadder
            self.pending_label = operand.upper()
        elif op in COILS and operand:
            self.outputs.append(Output("coil", f"{COILS[op]}{operand}"))
        elif op.endswith("(") and op[:-1] in BIT_OPS:
            self._start_logic()
            self.stack.append((self.terms, self.cur, op[:-1], BIT_OPS[op[:-1]]))
            self.terms, self.cur = [], None
        elif op == ")":
            if not self.stack:
                raise NotLadder
            inner = self._total()
            self.terms, self.cur, base, negated = self.stack.pop()
            if inner is None:
                raise NotLadder
            if negated:
                inner = _series(inner, Node("box", "NOT"))
            if base in AND_OPS:
                self._and_with(inner)
            else:
                self._or_with(inner)
        elif op in AND_OPS and operand:
            self._start_logic()
            self._and_with(Node("nc" if AND_OPS[op] else "no", operand))
        elif op in OR_OPS and operand:
            self._start_logic()
            self._or_with(Node("nc" if OR_OPS[op] else "no", operand))
        elif op == "O":
            self._start_logic()
            self.terms.append(self.cur)
            self.cur = None
        elif op in EDGE_OPS and operand:
            self._start_logic()
            self.cur, self.terms = _series(self._total(), Node("box", f"{EDGE_OPS[op]} {operand}")), []
        elif op == "NOT":
            self._start_logic()
            self.cur, self.terms = _series(self._total(), Node("box", "NOT")), []
        else:
            raise NotLadder

    def finish(self) -> list[Rung]:
        unused_load = self.loads and not self.loads_used
        if self.stack or unused_load or self.pending_label is not None or not self.outputs:
            raise NotLadder
        self.rungs.append(Rung(self._total(), self.outputs))
        return self.rungs


_STATEMENT = re.compile(r"^(\S+?\(?)(?:\s+(.*))?$")
_LABEL = re.compile(r"^(\w+)\s*:\s*(.*)$")


def _clean_operand(text: str) -> str:
    text = " ".join(text.split()).rstrip(" ;")
    return text[1:-1] if re.fullmatch(r'"[^"]*"', text) else text


def _parse_rungs(statements: list[str]) -> list[Rung]:
    parser = _Parser()
    for stmt in statements:
        label = None
        if m := _LABEL.match(stmt):
            label, stmt = m[1].upper(), m[2].strip()
        m = _STATEMENT.match(stmt) if stmt else None
        if stmt and not m:
            raise NotLadder
        parser.feed(label, m[1].upper() if m else "", _clean_operand(m[2] or "") if m else "")
    return parser.finish()


def parse_block(source: str) -> tuple[str, list[Network]]:
    """Split a block's STL source into its header line and networks, with rungs where they can be drawn."""
    header = ""
    networks: list[Network] = []
    current: Network | None = None
    statements: list[str] = []
    call: str | None = None                    # a CALL whose parameter list spans several lines
    in_code = False

    def close() -> None:
        if current is None:
            return
        if statements:
            try:
                current.rungs = _parse_rungs(statements)
            except NotLadder:
                current.rungs = None
        else:
            current.rungs = []
        networks.append(current)

    for line in source.splitlines():
        text = line.strip()
        upper = text.upper()
        if not in_code:
            if upper == "BEGIN":
                in_code = True
            elif text and not header and not upper.startswith(("VAR", "END_VAR", "TITLE", "VERSION")):
                header = text
            continue
        if upper == "NETWORK":
            close()
            current, statements, call = Network(len(networks) + 1, "", [], [], []), [], None
        elif current is None:
            continue
        elif upper.startswith("TITLE"):
            current.title = text.split("=", 1)[1].strip() if "=" in text else ""
        elif text.startswith("//"):
            current.comments.append(text[2:].strip())
        elif upper.startswith("END_") and not upper.startswith("END_VAR"):
            break
        elif text:
            current.stl.append(text)
            code = text.split("//", 1)[0].strip()
            if call is not None:
                call += " " + code
                if ")" in code:
                    statements.append(call.rstrip(" ;"))
                    call = None
            elif re.match(r"(\w+\s*:\s*)?CALL\b", code, re.I) and "(" in code and ")" not in code:
                call = code
            elif code.rstrip(" ;"):
                statements.append(code.rstrip(" ;"))
    close()
    return header, networks


# ------------------------------------------------------------------ text output

def _leaf_text(node: Node) -> str:
    return {"no": f"--| {node.label} |--", "nc": f"--|/| {node.label} |--"}.get(node.kind, f"--[ {node.label} ]--")


def _text_rows(node: Node) -> list[str]:
    """Rows of equal width; row 0 carries the wire."""
    if node.kind in ("no", "nc", "box"):
        return [_leaf_text(node)]
    blocks = [_text_rows(c) for c in node.children]
    if node.kind == "series":
        height = max(len(b) for b in blocks)
        return ["".join(b[r] if r < len(b) else " " * len(b[0]) for b in blocks) for r in range(height)]
    width = max(len(b[0]) for b in blocks)
    return _stack([[b[0].ljust(width, "-")] + [row.ljust(width) for row in b[1:]] for b in blocks], right_rail=True)


def _stack(blocks: list[list[str]], right_rail: bool) -> list[str]:
    if len(blocks) == 1:
        return blocks[0]
    wire_rows, rows = [], []
    for b in blocks:
        wire_rows.append(len(rows))
        rows += b
    out = []
    for r, row in enumerate(rows):
        rail = "+" if r in wire_rows else "|" if r < wire_rows[-1] else " "
        out.append(rail + row + (rail if right_rail else ""))
    return out


def _rung_text(rung: Rung) -> list[str]:
    cells = [f"--( {o.label} )" if o.kind == "coil" else f"--[ {o.label} ]" for o in rung.outputs]
    width = max(len(c) for c in cells)
    outputs = _stack([[c.ljust(width)] for c in cells], right_rail=False)
    logic = _text_rows(rung.logic) if rung.logic else ["--"]
    rows = []
    for r in range(max(len(logic), len(outputs))):
        left = logic[r] if r < len(logic) else " " * len(logic[0])
        rows.append(("|" if r == 0 else " ") + left + (outputs[r] if r < len(outputs) else ""))
    return [row.rstrip() for row in rows]


def stl_to_ladder(source: str) -> str:
    """Convert a block's STL source into text with ladder rungs (or the original STL) per network."""
    header, networks = parse_block(source)
    out = [header, ""] if header else []
    for net in networks:
        out.append(f"Network {net.number}: {net.title}".rstrip(": ").rstrip())
        out.extend("  // " + c for c in net.comments)
        if net.rungs is None:
            out.append("  [STL - not drawable as ladder]")
            out.extend("  " + line for line in net.stl)
        elif not net.rungs:
            out.append("  (empty)")
        else:
            for rung in net.rungs:
                out.extend("  " + row for row in _rung_text(rung))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------- SVG output

ROW = 50          # height of one rung row
TOP = 30          # distance from a row's top to its wire
CHAR = 7.4        # approximate character width of the label font
PAD = 16          # width of a parallel branch's rails


def _leaf_width(label: str, boxed: bool) -> float:
    return max(64.0, len(label) * CHAR + (40 if boxed else 24))


def _measure(node: Node) -> tuple[float, int]:
    if node.kind in ("no", "nc"):
        return _leaf_width(node.label, False), 1
    if node.kind == "box":
        return _leaf_width(node.label, True), 1
    sizes = [_measure(c) for c in node.children]
    if node.kind == "series":
        return sum(w for w, _ in sizes), max(r for _, r in sizes)
    return max(w for w, _ in sizes) + 2 * PAD, sum(r for _, r in sizes)


def _line(x1: float, y1: float, x2: float, y2: float) -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'


def _label(x: float, y: float, text: str) -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}">{html.escape(text)}</text>'


def _box(x: float, y: float, width: float, label: str, parts: list[str], wire_out: bool = True) -> None:
    """A labelled rectangle centred in a cell that starts at x; the wire runs up to it and, unless it ends the rung, on."""
    inner = len(label) * CHAR + 16
    left = x + (width - inner) / 2
    parts.append(_line(x, y, left, y))
    if wire_out:
        parts.append(_line(left + inner, y, x + width, y))
    parts.append(f'<rect x="{left:.1f}" y="{y - 12:.1f}" width="{inner:.1f}" height="24" rx="3"/>')
    parts.append(_label(left + inner / 2, y + 4, label))


def _draw(node: Node, x: float, y: float, width: float, parts: list[str]) -> None:
    """Draw node with its wire at height y, filling exactly `width`."""
    if node.kind in ("no", "nc"):
        cx = x + width / 2
        parts.append(_line(x, y, cx - 6, y))
        parts.append(_line(cx + 6, y, x + width, y))
        parts.append(_line(cx - 6, y - 10, cx - 6, y + 10))
        parts.append(_line(cx + 6, y - 10, cx + 6, y + 10))
        if node.kind == "nc":
            parts.append(_line(cx - 5, y + 9, cx + 5, y - 9))
        parts.append(_label(cx, y - 15, node.label))
    elif node.kind == "box":
        _box(x, y, width, node.label, parts)
    elif node.kind == "series":
        cursor = x
        for child in node.children:
            w = _measure(child)[0]
            _draw(child, cursor, y, w, parts)
            cursor += w
        parts.append(_line(cursor, y, x + width, y))
    else:
        row = 0
        last_y = y
        for child in node.children:
            last_y = y + row * ROW
            _draw(child, x + PAD, last_y, width - 2 * PAD, parts)
            row += _measure(child)[1]
        parts.append(_line(x, y, x + PAD, y))
        parts.append(_line(x + width - PAD, y, x + width, y))
        parts.append(_line(x + PAD, y, x + PAD, last_y))
        parts.append(_line(x + width - PAD, y, x + width - PAD, last_y))


def _draw_output(out: Output, x: float, y: float, width: float, parts: list[str]) -> None:
    if out.kind == "box":
        _box(x, y, width, out.label, parts, wire_out=False)
        return
    letter, _, name = out.label.rpartition(" ") if out.label[:2] in ("S ", "R ") else ("", "", out.label)
    cx = x + width / 2
    parts.append(_line(x, y, cx - 9, y))
    parts.append(f'<path d="M {cx - 5:.1f} {y - 10:.1f} Q {cx - 13:.1f} {y:.1f} {cx - 5:.1f} {y + 10:.1f}"/>')
    parts.append(f'<path d="M {cx + 5:.1f} {y - 10:.1f} Q {cx + 13:.1f} {y:.1f} {cx + 5:.1f} {y + 10:.1f}"/>')
    if letter:
        parts.append(_label(cx, y + 4, letter))
    parts.append(_label(cx, y - 15, name))


def _rung_svg(rung: Rung) -> str:
    logic_w, logic_rows = _measure(rung.logic) if rung.logic else (40.0, 1)
    out_w = max(_leaf_width(o.label, o.kind == "box") for o in rung.outputs) + PAD
    rows = max(logic_rows, len(rung.outputs))
    left, width, height = 8.0, logic_w + out_w + 16, rows * ROW
    parts: list[str] = [_line(left, 6, left, height - 6)]
    if rung.logic:
        _draw(rung.logic, left, TOP, logic_w, parts)
    else:
        parts.append(_line(left, TOP, left + logic_w, TOP))
    ox = left + logic_w
    for n, out in enumerate(rung.outputs):
        y = TOP + n * ROW
        parts.append(_line(ox, y, ox + PAD, y))
        _draw_output(out, ox + PAD, y, out_w - PAD, parts)
    if len(rung.outputs) > 1:
        parts.append(_line(ox, TOP, ox, TOP + (len(rung.outputs) - 1) * ROW))
    return (f'<svg class="rung" width="{width + left:.0f}" height="{height}" viewBox="0 0 {width + left:.0f} {height}" '
            f'role="img">{"".join(parts)}</svg>')


_PAGE_STYLE = """
:root { color-scheme: light dark; --fg: #1c2733; --muted: #5b6876; --line: #d5dbe2; --bg: #ffffff; --panel: #f5f7f9; }
@media (prefers-color-scheme: dark) { :root { --fg: #e4e9ee; --muted: #9aa7b4; --line: #323c47; --bg: #14181d; --panel: #1b2027; } }
body { margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--fg); font: 14px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 24px; }
section { border-top: 1px solid var(--line); padding: 14px 0 10px; }
h2 { font-size: 15px; margin: 0 0 4px; }
.comment { color: var(--muted); margin: 0 0 8px; white-space: pre-wrap; }
.scroll { overflow-x: auto; }
svg.rung { display: block; margin: 4px 0; }
svg.rung line, svg.rung path, svg.rung rect { stroke: var(--fg); stroke-width: 1.5; fill: none; }
svg.rung text { fill: var(--fg); font: 12px Consolas, "Cascadia Mono", monospace; text-anchor: middle; }
pre { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; margin: 4px 0; overflow-x: auto; font: 12px Consolas, monospace; }
.tag { color: var(--muted); font-size: 12px; }
"""


def stl_to_ladder_html(source: str, title: str) -> str:
    """Convert a block's STL source into a standalone HTML page with one drawn rung (or STL) per network."""
    header, networks = parse_block(source)
    body = [f"<h1>{html.escape(title)}</h1>", f'<p class="sub">{html.escape(header)}</p>']
    for net in networks:
        body.append(f"<section><h2>Network {net.number}{': ' + html.escape(net.title) if net.title else ''}</h2>")
        if net.comments:
            body.append(f'<p class="comment">{html.escape(chr(10).join(net.comments))}</p>')
        if net.rungs is None:
            body.append('<div class="tag">Shown as STL (not drawable as ladder)</div>')
            body.append(f"<pre>{html.escape(chr(10).join(net.stl))}</pre>")
        elif not net.rungs:
            body.append('<div class="tag">Empty network</div>')
        else:
            body.extend(f'<div class="scroll">{_rung_svg(rung)}</div>' for rung in net.rungs)
        body.append("</section>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{_PAGE_STYLE}</style></head>"
            f"<body><main>{''.join(body)}</main></body></html>")
