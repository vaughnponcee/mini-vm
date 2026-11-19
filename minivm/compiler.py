"""AST -> bytecode. Resolves every name at compile time to a local slot,
an upvalue index, or a global (lexical/static scoping)."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ast as A
from . import bytecode as B
from .bytecode import Function


class CompileError(Exception):
    def __init__(self, msg: str, line: int):
        super().__init__(f"line {line}: {msg}")
        self.line = line


@dataclass
class Local:
    name: str
    depth: int       # -1 while its initializer is being compiled
    captured: bool = False


@dataclass
class Loop:
    scope_depth: int
    breaks: list = field(default_factory=list)
    continues: list = field(default_factory=list)


class FnState:
    def __init__(self, fn: Function, enclosing: "FnState | None"):
        self.fn = fn
        self.enclosing = enclosing
        self.locals: list[Local] = [Local("", 0)]  # slot 0 = the closure being called
        self.upvalues: list[tuple[bool, int]] = []
        self.depth = 0
        self.loops: list[Loop] = []


BINOPS = {"+": B.ADD, "-": B.SUB, "*": B.MUL, "/": B.DIV, "%": B.MOD, "==": B.EQ, "!=": B.NE,
          "<": B.LT, "<=": B.LE, ">": B.GT, ">=": B.GE}


class Compiler:
    def __init__(self):
        self.s: FnState | None = None
        self.line = 1

    # -- emit helpers
    def emit(self, *xs: int) -> int:
        code = self.s.fn.code
        at = len(code)
        code.extend(xs)
        self.s.fn.lines.extend([self.line] * len(xs))
        return at

    def jump(self, op: int) -> int:
        return self.emit(op, -1) + 1  # address of the operand to patch

    def patch(self, operand_at: int, target: int | None = None) -> None:
        self.s.fn.code[operand_at] = len(self.s.fn.code) if target is None else target

    def const(self, v) -> int:
        consts = self.s.fn.consts
        for i, c in enumerate(consts):  # dedupe; type check keeps 1 != True != 1.0
            if type(c) is type(v) and c == v and not isinstance(v, Function):
                return i
        consts.append(v)
        return len(consts) - 1

    # -- scopes
    def begin(self):
        self.s.depth += 1

    def end(self):
        self.s.depth -= 1
        locs = self.s.locals
        while locs and locs[-1].depth > self.s.depth:
            self.emit(B.CLOSE_UPVAL if locs.pop().captured else B.POP)

    def pop_to(self, depth: int):
        """Emit pops for locals deeper than `depth` without forgetting them
        (used by break/continue, which leave the scope only on one path)."""
        for loc in reversed(self.s.locals):
            if loc.depth <= depth:
                break
            self.emit(B.CLOSE_UPVAL if loc.captured else B.POP)

    def declare(self, name: str, line: int):
        for loc in reversed(self.s.locals):
            if loc.depth != -1 and loc.depth < self.s.depth:
                break
            if loc.name == name:
                raise CompileError(f"'{name}' already declared in this scope", line)
        self.s.locals.append(Local(name, -1))

    # -- name resolution
    @staticmethod
    def resolve_local(s: FnState, name: str, line: int) -> int:
        for i in range(len(s.locals) - 1, -1, -1):
            if s.locals[i].name == name:
                if s.locals[i].depth == -1:
                    raise CompileError(f"can't read '{name}' in its own initializer", line)
                return i
        return -1

    def resolve_upvalue(self, s: FnState, name: str, line: int) -> int:
        if s.enclosing is None:
            return -1
        local = self.resolve_local(s.enclosing, name, line)
        if local != -1:
            s.enclosing.locals[local].captured = True
            return self.add_upvalue(s, True, local)
        up = self.resolve_upvalue(s.enclosing, name, line)
        if up != -1:
            return self.add_upvalue(s, False, up)
        return -1

    @staticmethod
    def add_upvalue(s: FnState, is_local: bool, index: int) -> int:
        key = (is_local, index)
        if key in s.upvalues:
            return s.upvalues.index(key)
        s.upvalues.append(key)
        s.fn.upvalue_count = len(s.upvalues)
        return len(s.upvalues) - 1

    def load(self, name: str, line: int):
        i = self.resolve_local(self.s, name, line)
        if i != -1:
            return self.emit(B.GET_LOCAL, i)
        i = self.resolve_upvalue(self.s, name, line)
        if i != -1:
            return self.emit(B.GET_UPVAL, i)
        self.emit(B.GET_GLOBAL, self.const(name))

    def store(self, name: str, line: int):
        i = self.resolve_local(self.s, name, line)
        if i != -1:
            return self.emit(B.SET_LOCAL, i)
        i = self.resolve_upvalue(self.s, name, line)
        if i != -1:
            return self.emit(B.SET_UPVAL, i)
        self.emit(B.SET_GLOBAL, self.const(name))

    # -- entry points
    def compile_program(self, prog: A.Program, repl: bool = False) -> Function:
        fn = Function("<script>")
        self.s = FnState(fn, None)
        for k, stmt in enumerate(prog.body):
            if repl and k == len(prog.body) - 1 and isinstance(stmt, A.ExprStmt):
                self.line = stmt.line
                self.expr(stmt.expr)
                self.emit(B.PRINT_RESULT)
            else:
                self.stmt(stmt)
        self.emit(B.NIL, B.RETURN)
        return fn

    # -- statements
    def stmt(self, n):
        self.line = n.line
        m = getattr(self, "s_" + type(n).__name__)
        m(n)

    def s_ExprStmt(self, n: A.ExprStmt):
        self.expr(n.expr)
        self.emit(B.POP)

    def s_Let(self, n: A.Let):
        if self.s.depth == 0:  # global: late-bound by name
            self.expr(n.value) if n.value else self.emit(B.NIL)
            self.emit(B.DEF_GLOBAL, self.const(n.name))
            return
        self.declare(n.name, n.line)
        if isinstance(n.value, A.FnExpr):
            self.s.locals[-1].depth = self.s.depth  # allow local recursion
        self.expr(n.value) if n.value else self.emit(B.NIL)
        self.s.locals[-1].depth = self.s.depth

    def s_Block(self, n: A.Block):
        self.begin()
        for st in n.body:
            self.stmt(st)
        self.end()

    def s_If(self, n: A.If):
        self.expr(n.cond)
        to_else = self.jump(B.JUMP_IF_FALSE)
        self.stmt(n.then)
        if n.other:
            to_end = self.jump(B.JUMP)
            self.patch(to_else)
            self.stmt(n.other)
            self.patch(to_end)
        else:
            self.patch(to_else)

    def s_While(self, n: A.While):
        start = len(self.s.fn.code)
        self.line = n.line
        self.expr(n.cond)
        exit_ = self.jump(B.JUMP_IF_FALSE)
        loop = Loop(self.s.depth)
        self.s.loops.append(loop)
        self.stmt(n.body)
        self.s.loops.pop()
        for c in loop.continues:
            self.patch(c)
        if n.step:
            self.stmt(n.step)
        self.emit(B.LOOP, start)
        self.patch(exit_)
        for b in loop.breaks:
            self.patch(b)

    def s_Break(self, n):
        loop = self.s.loops[-1]
        self.pop_to(loop.scope_depth)
        loop.breaks.append(self.jump(B.JUMP))

    def s_Continue(self, n):
        loop = self.s.loops[-1]
        self.pop_to(loop.scope_depth)
        loop.continues.append(self.jump(B.JUMP))

    def s_Return(self, n: A.Return):
        self.expr(n.value) if n.value else self.emit(B.NIL)
        self.emit(B.RETURN)

    # -- expressions
    def expr(self, n):
        self.line = n.line
        getattr(self, "e_" + type(n).__name__)(n)

    def e_Literal(self, n):
        v = n.value
        if v is None:
            self.emit(B.NIL)
        elif v is True:
            self.emit(B.TRUE)
        elif v is False:
            self.emit(B.FALSE)
        else:
            self.emit(B.CONST, self.const(v))

    def e_Var(self, n):
        self.load(n.name, n.line)

    def e_Assign(self, n):
        self.expr(n.value)
        self.line = n.line
        self.store(n.name, n.line)

    def e_Unary(self, n):
        self.expr(n.operand)
        self.line = n.line
        self.emit(B.NEG if n.op == "-" else B.NOT)

    def e_Binary(self, n):
        self.expr(n.left)
        self.expr(n.right)
        self.line = n.line
        self.emit(BINOPS[n.op])

    def e_Logical(self, n):
        self.expr(n.left)
        j = self.jump(B.JUMP_IF_FALSE_KEEP if n.op == "and" else B.JUMP_IF_TRUE_KEEP)
        self.emit(B.POP)
        self.expr(n.right)
        self.patch(j)

    def e_Call(self, n):
        self.expr(n.callee)
        for a in n.args:
            self.expr(a)
        self.line = n.line
        self.emit(B.CALL, len(n.args))

    def e_ListLit(self, n):
        for it in n.items:
            self.expr(it)
        self.emit(B.BUILD_LIST, len(n.items))

    def e_Index(self, n):
        self.expr(n.target)
        self.expr(n.index)
        self.line = n.line
        self.emit(B.INDEX)

    def e_SetIndex(self, n):
        self.expr(n.target)
        self.expr(n.index)
        self.expr(n.value)
        self.line = n.line
        self.emit(B.SET_INDEX)

    def e_FnExpr(self, n: A.FnExpr):
        fn = Function(n.name, len(n.params))
        outer = self.s
        self.s = FnState(fn, outer)
        self.begin()
        for p in n.params:
            self.s.locals.append(Local(p, self.s.depth))
        for st in n.body:
            self.stmt(st)
        self.line = n.line
        self.emit(B.NIL, B.RETURN)
        ups = self.s.upvalues
        self.s = outer
        self.line = n.line
        operands = [self.const(fn), len(ups)]
        for is_local, idx in ups:
            operands += [1 if is_local else 0, idx]
        self.emit(B.CLOSURE, *operands)


def compile_source(src: str, repl: bool = False) -> Function:
    from .parser import parse
    return Compiler().compile_program(parse(src), repl)
