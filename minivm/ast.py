"""AST node types. Every node carries the source line it starts on."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    line: int


# expressions
@dataclass
class Literal(Node):
    value: object


@dataclass
class Var(Node):
    name: str


@dataclass
class Assign(Node):
    name: str
    value: Node


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Logical(Node):
    op: str  # and | or
    left: Node
    right: Node


@dataclass
class Call(Node):
    callee: Node
    args: list


@dataclass
class ListLit(Node):
    items: list


@dataclass
class Index(Node):
    target: Node
    index: Node


@dataclass
class SetIndex(Node):
    target: Node
    index: Node
    value: Node


@dataclass
class FnExpr(Node):
    name: str
    params: list
    body: list


# statements
@dataclass
class ExprStmt(Node):
    expr: Node


@dataclass
class Let(Node):
    name: str
    value: Node | None


@dataclass
class Block(Node):
    body: list


@dataclass
class If(Node):
    cond: Node
    then: Node
    other: Node | None


@dataclass
class While(Node):
    cond: Node
    body: Node
    step: Node | None = None  # for-loops desugar into While with a step


@dataclass
class Return(Node):
    value: Node | None


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Program:
    body: list = field(default_factory=list)
