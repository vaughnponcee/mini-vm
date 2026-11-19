"""Recursive-descent parser with precedence climbing for binary operators.

Grammar (EBNF):
  program    = decl* EOF
  decl       = "let" IDENT ["=" expr] ";" | "fn" IDENT "(" params ")" block | stmt
  stmt       = "if" "(" expr ")" stmt ["else" stmt]
             | "while" "(" expr ")" stmt
             | "for" "(" [decl-or-expr] ";" [expr] ";" [expr] ")" stmt
             | "return" [expr] ";" | "break" ";" | "continue" ";"
             | block | expr ";"
  expr       = assignment
  assignment = (IDENT | postfix "[" expr "]") "=" assignment | or
  or         = and ("or" and)*          and = equality ("and" equality)*
  equality   = compare (("=="|"!=") compare)*
  compare    = term (("<"|">"|"<="|">=") term)*
  term       = factor (("+"|"-") factor)*    factor = unary (("*"|"/"|"%") unary)*
  unary      = ("-"|"!"|"not") unary | postfix
  postfix    = primary ("(" args ")" | "[" expr "]")*
  primary    = NUM | STR | true | false | nil | IDENT | "(" expr ")"
             | "[" [expr ("," expr)*] "]" | "fn" "(" params ")" block
"""
from __future__ import annotations

from . import ast as A
from .lexer import Token, tokenize


class ParseError(Exception):
    def __init__(self, msg: str, tok: Token):
        where = "end of input" if tok.kind == "EOF" else repr(tok.text)
        super().__init__(f"line {tok.line}:{tok.col}: {msg} (at {where})")
        self.line = tok.line


class Parser:
    def __init__(self, tokens: list[Token]):
        self.t = tokens
        self.i = 0
        self.loop_depth = 0
        self.fn_depth = 0

    # -- helpers
    def peek(self) -> Token:
        return self.t[self.i]

    def check(self, kind: str, text: str | None = None) -> bool:
        tok = self.t[self.i]
        return tok.kind == kind and (text is None or tok.text == text)

    def match(self, kind: str, *texts: str) -> Token | None:
        tok = self.t[self.i]
        if tok.kind == kind and (not texts or tok.text in texts):
            self.i += 1
            return tok
        return None

    def expect(self, kind: str, text: str | None = None, msg: str = "") -> Token:
        if self.check(kind, text):
            tok = self.t[self.i]
            self.i += 1
            return tok
        raise ParseError(msg or f"expected {text or kind}", self.peek())

    # -- statements
    def program(self) -> A.Program:
        body = []
        while not self.check("EOF"):
            body.append(self.decl())
        return A.Program(body)

    def decl(self):
        if self.check("KW", "let"):
            return self.let_decl()
        if self.check("KW", "fn") and self.t[self.i + 1].kind == "IDENT":
            tok = self.expect("KW", "fn")
            name = self.expect("IDENT").text
            fn = self.fn_rest(name, tok.line)
            # `fn f() {}` is sugar for `let f = fn() {}`; binding before the body
            # is compiled lets the function call itself recursively.
            return A.Let(tok.line, name, fn)
        return self.stmt()

    def let_decl(self):
        tok = self.expect("KW", "let")
        name = self.expect("IDENT", msg="expected variable name").text
        value = self.expr() if self.match("OP", "=") else None
        self.expect("OP", ";", "expected ';' after let")
        return A.Let(tok.line, name, value)

    def fn_rest(self, name: str, line: int) -> A.FnExpr:
        self.expect("OP", "(", "expected '(' after fn")
        params = []
        if not self.check("OP", ")"):
            while True:
                p = self.expect("IDENT", msg="expected parameter name").text
                if p in params:
                    raise ParseError(f"duplicate parameter '{p}'", self.t[self.i - 1])
                params.append(p)
                if not self.match("OP", ","):
                    break
        self.expect("OP", ")", "expected ')' after parameters")
        saved = self.loop_depth
        self.loop_depth = 0
        self.fn_depth += 1
        body = self.block().body
        self.fn_depth -= 1
        self.loop_depth = saved
        return A.FnExpr(line, name, params, body)

    def block(self) -> A.Block:
        tok = self.expect("OP", "{", "expected '{'")
        body = []
        while not self.check("OP", "}") and not self.check("EOF"):
            body.append(self.decl())
        self.expect("OP", "}", "expected '}' to close block")
        return A.Block(tok.line, body)

    def stmt(self):
        tok = self.peek()
        if self.match("KW", "if"):
            self.expect("OP", "(", "expected '(' after if")
            cond = self.expr()
            self.expect("OP", ")", "expected ')' after condition")
            then = self.stmt()
            other = self.stmt() if self.match("KW", "else") else None
            return A.If(tok.line, cond, then, other)
        if self.match("KW", "while"):
            self.expect("OP", "(", "expected '(' after while")
            cond = self.expr()
            self.expect("OP", ")", "expected ')' after condition")
            return A.While(tok.line, cond, self.loop_body())
        if self.match("KW", "for"):
            return self.for_stmt(tok)
        if self.match("KW", "return"):
            if self.fn_depth == 0:
                raise ParseError("'return' outside function", tok)
            value = None if self.check("OP", ";") else self.expr()
            self.expect("OP", ";", "expected ';' after return")
            return A.Return(tok.line, value)
        if self.match("KW", "break", "continue"):
            if self.loop_depth == 0:
                raise ParseError(f"'{tok.text}' outside loop", tok)
            self.expect("OP", ";", f"expected ';' after {tok.text}")
            return A.Break(tok.line) if tok.text == "break" else A.Continue(tok.line)
        if self.check("OP", "{"):
            return self.block()
        e = self.expr()
        self.expect("OP", ";", "expected ';' after expression")
        return A.ExprStmt(tok.line, e)

    def loop_body(self):
        self.loop_depth += 1
        body = self.stmt()
        self.loop_depth -= 1
        return body

    def for_stmt(self, tok):
        # for (init; cond; step) body  =>  { init; while (cond) { body } step }
        self.expect("OP", "(", "expected '(' after for")
        if self.match("OP", ";"):
            init = None
        elif self.check("KW", "let"):
            init = self.let_decl()
        else:
            init = A.ExprStmt(tok.line, self.expr())
            self.expect("OP", ";", "expected ';'")
        cond = A.Literal(tok.line, True) if self.check("OP", ";") else self.expr()
        self.expect("OP", ";", "expected ';' after loop condition")
        step = None if self.check("OP", ")") else self.expr()
        self.expect("OP", ")", "expected ')' after for clauses")
        loop = A.While(tok.line, cond, self.loop_body(), A.ExprStmt(tok.line, step) if step else None)
        return A.Block(tok.line, [init, loop] if init else [loop])

    # -- expressions
    def expr(self):
        return self.assignment()

    def assignment(self):
        left = self.binary(0)
        if self.check("OP", "="):
            eq = self.peek()
            self.i += 1
            value = self.assignment()
            if isinstance(left, A.Var):
                return A.Assign(left.line, left.name, value)
            if isinstance(left, A.Index):
                return A.SetIndex(left.line, left.target, left.index, value)
            raise ParseError("invalid assignment target", eq)
        return left

    LEVELS = [("KW", ("or",)), ("KW", ("and",)), ("OP", ("==", "!=")),
              ("OP", ("<", ">", "<=", ">=")), ("OP", ("+", "-")), ("OP", ("*", "/", "%"))]

    def binary(self, level: int):
        if level == len(self.LEVELS):
            return self.unary()
        kind, ops = self.LEVELS[level]
        left = self.binary(level + 1)
        while True:
            tok = self.peek()
            hit = tok.text in ops and (tok.kind == kind or (tok.kind == "OP" and tok.text in ("and", "or")))
            if not hit:
                return left
            self.i += 1
            right = self.binary(level + 1)
            if tok.text in ("and", "or"):
                left = A.Logical(tok.line, tok.text, left, right)
            else:
                left = A.Binary(tok.line, tok.text, left, right)

    def unary(self):
        tok = self.peek()
        if self.match("OP", "-", "!") or self.match("KW", "not"):
            return A.Unary(tok.line, "-" if tok.text == "-" else "!", self.unary())
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            tok = self.peek()
            if self.match("OP", "("):
                args = []
                if not self.check("OP", ")"):
                    while True:
                        args.append(self.expr())
                        if not self.match("OP", ","):
                            break
                self.expect("OP", ")", "expected ')' after arguments")
                if len(args) > 255:
                    raise ParseError("more than 255 arguments", tok)
                e = A.Call(tok.line, e, args)
            elif self.match("OP", "["):
                idx = self.expr()
                self.expect("OP", "]", "expected ']'")
                e = A.Index(tok.line, e, idx)
            else:
                return e

    def primary(self):
        tok = self.peek()
        if self.match("NUM") or self.match("STR"):
            return A.Literal(tok.line, tok.value)
        if self.match("KW", "true", "false", "nil"):
            return A.Literal(tok.line, {"true": True, "false": False, "nil": None}[tok.text])
        if self.match("IDENT"):
            return A.Var(tok.line, tok.text)
        if self.match("OP", "("):
            e = self.expr()
            self.expect("OP", ")", "expected ')'")
            return e
        if self.match("OP", "["):
            items = []
            if not self.check("OP", "]"):
                while True:
                    items.append(self.expr())
                    if not self.match("OP", ","):
                        break
            self.expect("OP", "]", "expected ']' after list")
            return A.ListLit(tok.line, items)
        if self.match("KW", "fn"):
            return self.fn_rest("<anon>", tok.line)
        raise ParseError("expected expression", tok)


def parse(src: str) -> A.Program:
    return Parser(tokenize(src)).program()
