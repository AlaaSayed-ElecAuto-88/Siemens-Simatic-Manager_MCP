"""Draws STL networks as text ladder rungs.

STEP 7 has no ladder export, so this rebuilds rungs from the STL of bit-logic networks:
contacts (A/AN/O/ON, nested brackets), edges, NOT, coils (=, S, R), timers and counters.
Networks that use anything else (jumps, loads/transfers, calls, comparisons) stay as STL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# English and German (U/UN, ZV/ZR, SI/SV/SA) mnemonics
AND_OPS = {"A": False, "AN": True, "U": False, "UN": True}
OR_OPS = {"O": False, "ON": True}
COILS = {"=": "", "S": "S ", "R": "R "}
BOX_OPS = {"SD", "SE", "SP", "SS", "SF", "SI", "SV", "SA", "CU", "CD", "ZV", "ZR", "FR"}
EDGE_OPS = {"FP": "P", "FN": "N"}
IGNORED = {"NOP", "BLD"}


class NotLadder(Exception):
    pass


@dataclass
class Node:
    kind: str                      # contact | element | series | parallel
    text: str = ""
    children: list["Node"] = field(default_factory=list)


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


def _contact(operand: str, negated: bool) -> Node:
    return Node("contact", f"--|/| {operand} |--" if negated else f"--| {operand} |--")


@dataclass
class Rung:
    logic: Node | None
    outputs: list[str]


class _Parser:
    def __init__(self) -> None:
        self.rungs: list[Rung] = []
        self.terms: list[Node | None] = []     # OR-terms closed by a standalone "O"
        self.cur: Node | None = None           # the AND-chain being built
        self.stack: list[tuple[list[Node | None], Node | None, str, bool]] = []
        self.outputs: list[str] = []
        self.preset: str | None = None

    def _total(self) -> Node | None:
        return _parallel(*self.terms, self.cur)

    def _start_logic(self) -> None:
        if self.outputs:                        # logic after a coil starts a new rung
            self.rungs.append(Rung(self._total(), self.outputs))
            self.terms, self.cur, self.outputs = [], None, []

    def _or_with(self, node: Node) -> None:
        self.cur, self.terms = _parallel(self._total(), node), []

    def feed(self, op: str, operand: str) -> None:
        if op in IGNORED:
            return
        if op == "L" and self.preset is None:
            self.preset = operand
            return
        if op in BOX_OPS:
            self.outputs.append(f"--[ {op} {operand}{' ' + self.preset if self.preset else ''} ]")
            self.preset = None
            return
        if self.preset is not None:
            raise NotLadder
        if op in COILS and operand:
            self.outputs.append(f"--( {COILS[op]}{operand} )")
            return
        opens = op.endswith("(")
        base = op[:-1] if opens else op
        if opens and base in {**AND_OPS, **OR_OPS}:
            self._start_logic()
            self.stack.append((self.terms, self.cur, base, {**AND_OPS, **OR_OPS}[base]))
            self.terms, self.cur = [], None
        elif op == ")":
            if not self.stack:
                raise NotLadder
            inner = self._total()
            self.terms, self.cur, base, negated = self.stack.pop()
            if inner is None:
                raise NotLadder
            if negated:
                inner = _series(inner, Node("element", "--|NOT|--"))
            if base in AND_OPS:
                self.cur = _series(self.cur, inner)
            else:
                self._or_with(inner)
        elif op in AND_OPS and operand:
            self._start_logic()
            self.cur = _series(self.cur, _contact(operand, AND_OPS[op]))
        elif op in OR_OPS and operand:
            self._start_logic()
            self._or_with(_contact(operand, OR_OPS[op]))
        elif op == "O":
            self._start_logic()
            self.terms.append(self.cur)
            self.cur = None
        elif op in EDGE_OPS and operand:
            self._start_logic()
            self.cur, self.terms = _series(self._total(), Node("element", f"--[{EDGE_OPS[op]} {operand}]--")), []
        elif op == "NOT":
            self._start_logic()
            self.cur, self.terms = _series(self._total(), Node("element", "--|NOT|--")), []
        else:
            raise NotLadder

    def finish(self) -> list[Rung]:
        if self.stack or self.preset is not None or not self.outputs:
            raise NotLadder
        self.rungs.append(Rung(self._total(), self.outputs))
        return self.rungs


def _render(node: Node) -> list[str]:
    """Rows of equal width; row 0 carries the wire."""
    if node.kind in ("contact", "element"):
        return [node.text]
    blocks = [_render(c) for c in node.children]
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


def _render_rung(rung: Rung) -> list[str]:
    width = max(len(o) for o in rung.outputs)
    outputs = _stack([[o.ljust(width)] for o in rung.outputs], right_rail=False)
    logic = _render(rung.logic) if rung.logic else ["--"]
    height = max(len(logic), len(outputs))
    rows = []
    for r in range(height):
        left = logic[r] if r < len(logic) else " " * len(logic[0])
        rows.append(("|" if r == 0 else " ") + left + (outputs[r] if r < len(outputs) else ""))
    return [row.rstrip() for row in rows]


_STATEMENT = re.compile(r"^(\S+?\(?)(?:\s+(.*))?$")


def _clean_operand(text: str) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return " ".join(text.split())


def _network_to_ladder(statements: list[str]) -> list[str]:
    parser = _Parser()
    for stmt in statements:
        m = _STATEMENT.match(stmt)
        if not m:
            raise NotLadder
        parser.feed(m[1].upper(), _clean_operand(m[2] or ""))
    lines: list[str] = []
    for rung in parser.finish():
        lines += _render_rung(rung)
    return lines


def stl_to_ladder(source: str) -> str:
    """Convert a block's STL source into text with one ladder rung (or the original STL) per network."""
    out: list[str] = []
    number = 0
    title, comments, statements, raw = "", [], [], []
    in_code = False

    def flush() -> None:
        if not number:
            return
        out.append(f"Network {number}: {title}".rstrip(": ").rstrip())
        out.extend(comments)
        if not statements:
            out.append("  (empty)")
        else:
            try:
                out.extend("  " + line for line in _network_to_ladder(statements))
            except NotLadder:
                out.append("  [STL - not drawable as ladder]")
                out.extend("  " + line for line in raw)
        out.append("")

    for line in source.splitlines():
        text = line.strip()
        if not in_code:
            if text.upper() == "BEGIN":
                in_code = True
            elif text and not text.upper().startswith(("VAR", "END_VAR")) and number == 0 and not out:
                out.append(text)          # block header line, e.g. FUNCTION FC 1 : VOID
                out.append("")
            continue
        if text.upper() == "NETWORK":
            flush()
            number += 1
            title, comments, statements, raw = "", [], [], []
        elif text.upper().startswith("TITLE"):
            title = text.split("=", 1)[1].strip() if "=" in text else ""
        elif text.startswith("//"):
            comments.append("  " + text)
        elif text.upper().startswith("END_") and not text.upper().startswith("END_VAR"):
            break
        elif text:
            code = text.split("//", 1)[0].strip().rstrip(";").strip()
            if code:
                statements.append(code)
                raw.append(text)
    flush()
    return "\n".join(out).rstrip() + "\n"
