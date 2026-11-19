"""Instruction set, function objects and the disassembler.

Code is a flat list of ints: an opcode followed by its operands. Operands are
whole Python ints (no byte packing), which keeps the VM loop simple.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# name: number of operands
OPS = {
    "CONST": 1,          # push consts[a]
    "NIL": 0, "TRUE": 0, "FALSE": 0,
    "POP": 0,
    "GET_LOCAL": 1, "SET_LOCAL": 1,      # slot relative to frame base
    "GET_GLOBAL": 1, "SET_GLOBAL": 1, "DEF_GLOBAL": 1,  # operand = const index of name
    "GET_UPVAL": 1, "SET_UPVAL": 1,
    "CLOSE_UPVAL": 0,    # hoist top-of-stack local to the heap, then pop
    "ADD": 0, "SUB": 0, "MUL": 0, "DIV": 0, "MOD": 0, "NEG": 0, "NOT": 0,
    "EQ": 0, "NE": 0, "LT": 0, "LE": 0, "GT": 0, "GE": 0,
    "JUMP": 1,           # absolute target
    "JUMP_IF_FALSE": 1,  # pops the condition
    "JUMP_IF_FALSE_KEEP": 1, "JUMP_IF_TRUE_KEEP": 1,  # for and/or; condition stays
    "LOOP": 1,           # absolute target (backward)
    "CALL": 1,           # argc
    "CLOSURE": -1,       # const idx, upvalue count n, then n pairs (is_local, index)
    "RETURN": 0,
    "BUILD_LIST": 1, "INDEX": 0, "SET_INDEX": 0,
    "PRINT_RESULT": 0,   # REPL: print top of stack if not nil, then pop
}
NAMES = list(OPS)
OP = {name: i for i, name in enumerate(NAMES)}
globals().update(OP)  # CONST, NIL, ... as module constants


@dataclass(eq=False)
class Function:
    name: str
    arity: int = 0
    code: list = field(default_factory=list)
    consts: list = field(default_factory=list)
    lines: list = field(default_factory=list)  # parallel to code
    upvalue_count: int = 0

    def __repr__(self):
        return f"<fn {self.name}>"


def disassemble(fn: Function, out: list[str] | None = None, depth: int = 0) -> str:
    out = out if out is not None else []
    pad = "  " * depth
    out.append(f"{pad}== {fn.name} (arity {fn.arity}, upvalues {fn.upvalue_count}) ==")
    i = 0
    nested = []
    last_line = None
    while i < len(fn.code):
        op = fn.code[i]
        name = NAMES[op]
        line = fn.lines[i]
        ln = f"{line:4}" if line != last_line else "   |"
        last_line = line
        if name == "CLOSURE":
            idx, n = fn.code[i + 1], fn.code[i + 2]
            pairs = fn.code[i + 3:i + 3 + 2 * n]
            caps = " ".join(f"{'local' if pairs[k] else 'upval'}:{pairs[k + 1]}" for k in range(0, len(pairs), 2))
            out.append(f"{pad}{i:04} {ln} {name:<18} {idx} {fn.consts[idx]!r} {caps}")
            nested.append(fn.consts[idx])
            i += 3 + 2 * n
            continue
        argc = OPS[name]
        args = fn.code[i + 1:i + 1 + argc]
        note = ""
        if name in ("CONST", "GET_GLOBAL", "SET_GLOBAL", "DEF_GLOBAL"):
            note = f" ({fn.consts[args[0]]!r})"
        out.append(f"{pad}{i:04} {ln} {name:<18} {' '.join(map(str, args))}{note}".rstrip())
        i += 1 + argc
    for f in nested:
        disassemble(f, out, depth + 1)
    return "\n".join(out)
