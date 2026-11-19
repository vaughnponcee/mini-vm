"""Tokenizer. Produces (kind, text, value, line) tuples."""
from __future__ import annotations

from dataclasses import dataclass

KEYWORDS = {"let", "fn", "return", "if", "else", "while", "for", "true", "false", "nil",
            "and", "or", "not", "break", "continue"}
TWO_CHAR = {"==", "!=", "<=", ">=", "&&", "||"}
ONE_CHAR = set("+-*/%(){}[],;=<>!.")


@dataclass(frozen=True)
class Token:
    kind: str      # NUM STR IDENT KW OP EOF
    text: str
    value: object
    line: int
    col: int


class LexError(Exception):
    def __init__(self, msg: str, line: int, col: int):
        super().__init__(f"line {line}:{col}: {msg}")
        self.line, self.col = line, col


def tokenize(src: str) -> list[Token]:
    toks: list[Token] = []
    i, line, col = 0, 1, 1
    n = len(src)

    def adv(k: int = 1):
        nonlocal i, col
        i += k
        col += k

    while i < n:
        c = src[i]
        if c == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if c in " \t\r":
            adv()
            continue
        if c == "/" and src.startswith("//", i):
            while i < n and src[i] != "\n":
                adv()
            continue
        start_col = col
        if c.isdigit():
            j = i
            while j < n and (src[j].isdigit() or (src[j] == "." and j + 1 < n and src[j + 1].isdigit()
                                                 and "." not in src[i:j])):
                j += 1
            text = src[i:j]
            toks.append(Token("NUM", text, float(text) if "." in text else int(text), line, start_col))
            adv(j - i)
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            text = src[i:j]
            toks.append(Token("KW" if text in KEYWORDS else "IDENT", text, None, line, start_col))
            adv(j - i)
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and src[j] != '"':
                if src[j] == "\n":
                    raise LexError("unterminated string", line, start_col)
                if src[j] == "\\" and j + 1 < n:
                    esc = src[j + 1]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(esc, esc))
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            if j >= n:
                raise LexError("unterminated string", line, start_col)
            toks.append(Token("STR", src[i:j + 1], "".join(buf), line, start_col))
            adv(j + 1 - i)
            continue
        two = src[i:i + 2]
        if two in TWO_CHAR:
            toks.append(Token("OP", {"&&": "and", "||": "or"}.get(two, two), None, line, start_col))
            adv(2)
            continue
        if c in ONE_CHAR:
            toks.append(Token("OP", c, None, line, start_col))
            adv()
            continue
        raise LexError(f"unexpected character {c!r}", line, start_col)
    toks.append(Token("EOF", "", None, line, col))
    return toks
