"""Stack-based bytecode interpreter with call frames and heap-hoisted upvalues."""
from __future__ import annotations

import time

from . import bytecode as B
from .bytecode import NAMES, OPS, Function

MAX_FRAMES = 1000


class Closure:
    __slots__ = ("fn", "upvalues")

    def __init__(self, fn: Function, upvalues: list):
        self.fn, self.upvalues = fn, upvalues

    def __repr__(self):
        return f"<fn {self.fn.name}>"


class Upvalue:
    """Points at a stack slot while the variable is live ("open"); copies the
    value into itself when that slot goes away ("closed")."""
    __slots__ = ("index", "value", "closed")

    def __init__(self, index: int):
        self.index, self.value, self.closed = index, None, False


class Native:
    __slots__ = ("name", "arity", "fn")

    def __init__(self, name, arity, fn):
        self.name, self.arity, self.fn = name, arity, fn

    def __repr__(self):
        return f"<native {self.name}>"


class Frame:
    __slots__ = ("closure", "ip", "base")

    def __init__(self, closure, base):
        self.closure, self.ip, self.base = closure, 0, base


class VMError(Exception):
    def __init__(self, msg: str, trace: list[str]):
        super().__init__(msg)
        self.msg, self.trace = msg, trace

    def __str__(self):
        return "\n".join([f"runtime error: {self.msg}"] + self.trace)


def is_num(v) -> bool:
    return (type(v) is int or type(v) is float)


def truthy(v) -> bool:
    return v is not None and v is not False


def equal(a, b) -> bool:
    if type(a) is bool or type(b) is bool:
        return a is b
    if is_num(a) and is_num(b):
        return a == b
    if type(a) is not type(b):
        return False
    return a is b if isinstance(a, (Closure, Native, list)) else a == b


def show(v) -> str:
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if type(v) is float:
        return str(int(v)) if v.is_integer() and abs(v) < 1e16 else repr(v)
    if type(v) is list:
        return "[" + ", ".join(repr(x) if type(x) is str else show(x) for x in v) + "]"
    return str(v)


def type_name(v) -> str:
    if v is None:
        return "nil"
    if type(v) is bool:
        return "bool"
    if is_num(v):
        return "number"
    if type(v) is str:
        return "string"
    if type(v) is list:
        return "list"
    return "function"


class VM:
    def __init__(self, out=None):
        self.out = out if out is not None else print
        self.globals: dict = {}
        self.stack: list = []
        self.frames: list[Frame] = []
        self.open_upvalues: dict[int, Upvalue] = {}
        self.instructions = 0
        self._natives()

    def _natives(self):
        def native_print(*args):
            self.out(" ".join(show(a) for a in args))

        def native_len(x):
            if type(x) not in (str, list):
                raise TypeError(f"len() expects a string or list, got {type_name(x)}")
            return len(x)

        def native_push(lst, v):
            if type(lst) is not list:
                raise TypeError(f"push() expects a list, got {type_name(lst)}")
            lst.append(v)
            return lst

        def native_pop(lst):
            if type(lst) is not list or not lst:
                raise TypeError("pop() expects a non-empty list")
            return lst.pop()

        def native_assert(cond, msg="assertion failed"):
            if not truthy(cond):
                raise AssertionError(show(msg))

        for name, arity, fn in [
            ("print", -1, native_print), ("len", 1, native_len), ("str", 1, show),
            ("type", 1, type_name), ("clock", 0, time.perf_counter),
            ("push", 2, native_push), ("pop", 1, native_pop), ("assert", -1, native_assert),
            ("floor", 1, lambda x: int(x // 1)),
        ]:
            self.globals[name] = Native(name, arity, fn)

    # ---------------------------------------------------------------- errors
    def error(self, msg: str):
        trace = []
        for fr in reversed(self.frames):
            fn = fr.closure.fn
            line = fn.lines[max(0, fr.ip - 1)] if fn.lines else 0
            trace.append(f"  at {fn.name} (line {line})")
        self.stack.clear()
        self.frames.clear()
        self.open_upvalues.clear()
        raise VMError(msg, trace)

    # ---------------------------------------------------------------- calls
    def call_value(self, callee, argc: int):
        if type(callee) is Closure:
            fn = callee.fn
            if argc != fn.arity:
                self.error(f"{fn.name}() takes {fn.arity} argument(s), got {argc}")
            if len(self.frames) >= MAX_FRAMES:
                self.error(f"stack overflow (more than {MAX_FRAMES} nested calls)")
            self.frames.append(Frame(callee, len(self.stack) - argc - 1))
            return
        if type(callee) is Native:
            if callee.arity >= 0 and argc != callee.arity:
                self.error(f"{callee.name}() takes {callee.arity} argument(s), got {argc}")
            args = self.stack[len(self.stack) - argc:]
            try:
                result = callee.fn(*args)
            except (TypeError, AssertionError, ValueError, ZeroDivisionError) as e:
                self.error(f"{callee.name}(): {e}")
            del self.stack[len(self.stack) - argc - 1:]
            self.stack.append(result)
            return
        self.error(f"can only call functions, got {type_name(callee)}")

    def capture(self, index: int) -> Upvalue:
        uv = self.open_upvalues.get(index)
        if uv is None:
            uv = self.open_upvalues[index] = Upvalue(index)
        return uv

    def close_upvalues(self, from_index: int):
        for idx in [i for i in self.open_upvalues if i >= from_index]:
            uv = self.open_upvalues.pop(idx)
            uv.value = self.stack[idx]
            uv.closed = True

    # ---------------------------------------------------------------- run
    def run_function(self, fn: Function):
        closure = Closure(fn, [])
        self.stack.append(closure)
        self.call_value(closure, 0)
        return self.run()

    def run(self):  # noqa: C901 - a dispatch loop is one big switch by nature
        stack = self.stack
        push, pop = stack.append, stack.pop
        frame = self.frames[-1]
        code, consts = frame.closure.fn.code, frame.closure.fn.consts
        ip, base = frame.ip, frame.base
        count = 0
        while True:
            op = code[ip]
            ip += 1
            count += 1
            if op == B.GET_LOCAL:
                push(stack[base + code[ip]])
                ip += 1
            elif op == B.CONST:
                push(consts[code[ip]])
                ip += 1
            elif op == B.ADD or op == B.SUB or op == B.MUL or op == B.LT or op == B.GT \
                    or op == B.LE or op == B.GE or op == B.DIV or op == B.MOD:
                b = pop()
                a = pop()
                if is_num(a) and is_num(b):
                    if op == B.ADD:
                        push(a + b)
                    elif op == B.SUB:
                        push(a - b)
                    elif op == B.MUL:
                        push(a * b)
                    elif op == B.LT:
                        push(a < b)
                    elif op == B.GT:
                        push(a > b)
                    elif op == B.LE:
                        push(a <= b)
                    elif op == B.GE:
                        push(a >= b)
                    elif b == 0:
                        frame.ip = ip
                        self.error("division by zero" if op == B.DIV else "modulo by zero")
                    elif op == B.DIV:
                        push(a // b if type(a) is int and type(b) is int and a % b == 0 else a / b)
                    else:
                        push(a % b)
                elif op == B.ADD and type(a) is str and type(b) is str:
                    push(a + b)
                elif op == B.ADD and type(a) is list and type(b) is list:
                    push(a + b)
                elif op in (B.LT, B.GT, B.LE, B.GE) and type(a) is str and type(b) is str:
                    push({B.LT: a < b, B.GT: a > b, B.LE: a <= b, B.GE: a >= b}[op])
                else:
                    frame.ip = ip
                    sym = {B.ADD: "+", B.SUB: "-", B.MUL: "*", B.DIV: "/", B.MOD: "%",
                           B.LT: "<", B.GT: ">", B.LE: "<=", B.GE: ">="}[op]
                    self.error(f"bad operands for '{sym}': {type_name(a)} and {type_name(b)}")
            elif op == B.SET_LOCAL:
                stack[base + code[ip]] = stack[-1]
                ip += 1
            elif op == B.JUMP_IF_FALSE:
                if truthy(pop()):
                    ip += 1
                else:
                    ip = code[ip]
            elif op == B.LOOP or op == B.JUMP:
                ip = code[ip]
            elif op == B.POP:
                pop()
            elif op == B.GET_GLOBAL:
                name = consts[code[ip]]
                ip += 1
                try:
                    push(self.globals[name])
                except KeyError:
                    frame.ip = ip
                    self.error(f"undefined variable '{name}'")
            elif op == B.GET_UPVAL:
                uv = frame.closure.upvalues[code[ip]]
                ip += 1
                push(uv.value if uv.closed else stack[uv.index])
            elif op == B.SET_UPVAL:
                uv = frame.closure.upvalues[code[ip]]
                ip += 1
                if uv.closed:
                    uv.value = stack[-1]
                else:
                    stack[uv.index] = stack[-1]
            elif op == B.CALL:
                argc = code[ip]
                ip += 1
                frame.ip = ip
                self.call_value(stack[-argc - 1], argc)
                frame = self.frames[-1]
                code, consts = frame.closure.fn.code, frame.closure.fn.consts
                ip, base = frame.ip, frame.base
            elif op == B.RETURN:
                result = pop()
                self.close_upvalues(base)
                self.frames.pop()
                del stack[base:]
                if not self.frames:
                    self.instructions += count
                    return result
                push(result)
                frame = self.frames[-1]
                code, consts = frame.closure.fn.code, frame.closure.fn.consts
                ip, base = frame.ip, frame.base
            elif op == B.EQ:
                b = pop()
                push(equal(pop(), b))
            elif op == B.NE:
                b = pop()
                push(not equal(pop(), b))
            elif op == B.NIL:
                push(None)
            elif op == B.TRUE:
                push(True)
            elif op == B.FALSE:
                push(False)
            elif op == B.NOT:
                push(not truthy(pop()))
            elif op == B.NEG:
                v = pop()
                if not is_num(v):
                    frame.ip = ip
                    self.error(f"bad operand for unary '-': {type_name(v)}")
                push(-v)
            elif op == B.JUMP_IF_FALSE_KEEP:
                ip = ip + 1 if truthy(stack[-1]) else code[ip]
            elif op == B.JUMP_IF_TRUE_KEEP:
                ip = code[ip] if truthy(stack[-1]) else ip + 1
            elif op == B.DEF_GLOBAL:
                self.globals[consts[code[ip]]] = pop()
                ip += 1
            elif op == B.SET_GLOBAL:
                name = consts[code[ip]]
                ip += 1
                if name not in self.globals:
                    frame.ip = ip
                    self.error(f"assignment to undefined variable '{name}' (declare it with let)")
                self.globals[name] = stack[-1]
            elif op == B.CLOSURE:
                fn = consts[code[ip]]
                n = code[ip + 1]
                ip += 2
                ups = []
                for _ in range(n):
                    is_local, idx = code[ip], code[ip + 1]
                    ip += 2
                    ups.append(self.capture(base + idx) if is_local else frame.closure.upvalues[idx])
                push(Closure(fn, ups))
            elif op == B.CLOSE_UPVAL:
                self.close_upvalues(len(stack) - 1)
                pop()
            elif op == B.BUILD_LIST:
                n = code[ip]
                ip += 1
                items = stack[len(stack) - n:] if n else []
                del stack[len(stack) - n:]
                push(items)
            elif op == B.INDEX or op == B.SET_INDEX:
                value = pop() if op == B.SET_INDEX else None
                idx = pop()
                target = pop()
                frame.ip = ip
                if type(target) not in (list, str):
                    self.error(f"can't index {type_name(target)}")
                if type(idx) is not int:
                    self.error(f"index must be an integer, got {type_name(idx)}")
                if not -len(target) <= idx < len(target):
                    self.error(f"index {idx} out of range for length {len(target)}")
                if op == B.INDEX:
                    push(target[idx])
                else:
                    if type(target) is str:
                        self.error("strings are immutable")
                    target[idx] = value
                    push(value)
            elif op == B.PRINT_RESULT:
                v = pop()
                if v is not None:
                    self.out(repr(v) if type(v) is str else show(v))
            else:  # pragma: no cover
                raise RuntimeError(f"bad opcode {op} ({NAMES[op] if op < len(NAMES) else '?'})")


def run_source(src: str, out=None, vm: VM | None = None, repl: bool = False):
    from .compiler import compile_source
    vm = vm or VM(out)
    return vm.run_function(compile_source(src, repl))


assert all(OPS[n] >= -1 for n in NAMES)
