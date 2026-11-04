"""Checkpointed graphs (SUB-09, FWK-01): pause a V2-V5 run between two nodes and resume it from its last node.

Opt-in (HF_CHECKPOINT=sqlite|postgres, or `--checkpoint`). Off, a shape compiles and runs its StateGraph exactly as
before. On, the graph is compiled with one of LangGraph's own checkpoint savers (AsyncSqliteSaver offline,
AsyncPostgresSaver with the `checkpoint-postgres` extra) and run under one LangGraph thread id per run attempt, one
node per invocation (`interrupt_before="*"`), so the saver holds the graph state after every node. A pause rule can
then stop the run between two nodes (RunPaused); the runner resumes it by invoking the same thread again, and
LangGraph carries on from the node that was next. Nothing in the graph state is dumped or restored by hand.

What a resume restores, and from where:
  graph state (messages, stage / plan position, retries, the stop)   the checkpoint (LangGraph thread)
  node count against the recursion limit                             the checkpoint's step counter
  budget used so far, spans, tool violations, snapshot (plans),
  recoveries, delegate call count, the model backend                 the HarnessInstance, which is the same object:
                                                                     a resume happens within the same provision
  sandbox files, database and MCP sessions                           the same live sandbox (not torn down yet)

So a resumed run spends no step, tool call or token twice and gets none for free: the budget is never reset, the
recursion limit counts nodes across invocations, and the trace is one trace (plus a `resume` handoff span, which no
grader or cost counts). Pauses happen only between nodes, before the next node starts, so no model call or tool
call is ever repeated. A delegate's own graph (EXT-09) runs inside its parent's node and is not checkpointed.

Not supported: resuming in another process. The per-run objects above are not in the checkpoint and the sandbox is
torn down when the run ends; the checkpoint survives (it is in the saver's database) but a new run starts a new
thread. An exception raised inside a node is a crash as before, not a pause.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.errors import GraphRecursionError

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph import StateGraph

    from harness_factory.shapes.base import HarnessInstance

BACKENDS = ("sqlite", "postgres")

# Called before each node with (nodes run so far, the nodes about to run); True pauses the run there.
PauseRule = Callable[[int, tuple[str, ...]], bool]


def pause_after(n: int) -> PauseRule:
    """Pause once, after the run's first n nodes."""
    return lambda nodes, _next: nodes == n


def pause_every() -> PauseRule:
    """Pause before every node (each resume runs one node): the strictest resume test."""
    return lambda _nodes, _next: True


class RunPaused(Exception):
    """The run stopped between two nodes; its graph state is in the checkpoint under `thread_id`."""

    def __init__(self, thread_id: str, nodes: int, next_nodes: tuple[str, ...]) -> None:
        super().__init__(f"paused after {nodes} node(s), before {', '.join(next_nodes)} (thread {thread_id})")
        self.thread_id, self.nodes, self.next_nodes = thread_id, nodes, next_nodes


@dataclass(frozen=True)
class CheckpointConfig:
    """Which saver runs checkpoint into: `sqlite` (url = a file path) or `postgres` (url = a conninfo string)."""
    backend: str
    url: str

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(f"checkpoint backend must be one of {BACKENDS}, not {self.backend!r}")
        if not self.url:
            raise ValueError(f"checkpoint backend {self.backend} needs a url (HF_CHECKPOINT_URL)")


@dataclass
class Checkpoint:
    """One run's checkpointing: the open saver, its LangGraph thread and the pause rule."""
    saver: BaseCheckpointSaver
    thread_id: str
    backend: str
    pause: PauseRule | None = None
    resumes: int = 0
    resuming: bool = False         # set by the runner: the next invocation continues the thread

    @staticmethod
    def thread_for(run_key_short: str) -> str:
        """The RunKey says which experiment; the suffix tells apart attempts of the same one (a rerun, a retry)."""
        return f"{run_key_short}-{uuid.uuid4().hex[:8]}"

    def describe(self) -> dict[str, Any]:
        return {"backend": self.backend, "thread_id": self.thread_id, "resumes": self.resumes}


@asynccontextmanager
async def open_saver(config: CheckpointConfig) -> AsyncIterator[BaseCheckpointSaver]:
    """LangGraph's own async saver for `config`, closed on exit (including on cancel)."""
    if config.backend == "sqlite":
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        Path(config.url).parent.mkdir(parents=True, exist_ok=True)
        # Concurrent runs of a campaign each open the file: wait for the lock rather than fail (sqlite's 5 s default).
        async with aiosqlite.connect(config.url, timeout=30) as conn:
            yield AsyncSqliteSaver(conn)
        return
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as e:  # pragma: no cover - depends on the optional extra
        raise RuntimeError("HF_CHECKPOINT=postgres needs the checkpoint-postgres extra "
                           "(uv sync --extra checkpoint-postgres)") from e
    async with AsyncPostgresSaver.from_conn_string(config.url) as saver:
        await saver.setup()                     # idempotent: creates or migrates LangGraph's tables
        yield saver


async def run_graph(inst: HarnessInstance, builder: StateGraph, initial: dict[str, Any],
                    recursion_limit: int) -> dict[str, Any]:
    """Run a shape's graph to its end and return the final state. Every V2-V5 shape runs its graph through here.

    Without a checkpoint this is `builder.compile().ainvoke(...)`, as before. With one, the graph runs one node per
    invocation under the run's thread; between nodes the pause rule may raise RunPaused. A thread that already has
    an unfinished checkpoint is continued instead of started (that is the resume). A resume runs the pending node
    before the rule is asked again, so every resume makes progress."""
    cp = inst.checkpoint
    if cp is None:
        return await builder.compile().ainvoke(initial, config={"recursion_limit": recursion_limit})
    graph = builder.compile(checkpointer=cp.saver, interrupt_before="*")
    config = {"recursion_limit": recursion_limit, "configurable": {"thread_id": cp.thread_id}}
    state = await graph.aget_state(config)
    resumed = bool(state.next)
    if resumed != cp.resuming:
        raise RuntimeError(f"checkpoint thread {cp.thread_id}: "
                           + ("unfinished state found on a fresh start" if resumed else "nothing to resume"))
    cp.resuming = False
    payload: dict[str, Any] | None = None if resumed else initial
    while True:
        await graph.ainvoke(payload, config)          # stops before the next node: one node ran (none on the first)
        payload = None
        state = await graph.aget_state(config)
        nodes = int(state.metadata.get("step", 0))
        # LangGraph's own bound, counted across invocations: a plain ainvoke raises once `recursion_limit` steps
        # have run, so a checkpointed run raises at exactly the same node.
        if nodes >= recursion_limit:
            raise GraphRecursionError(f"Recursion limit of {recursion_limit} reached without hitting a stop "
                                      f"condition (checkpoint thread {cp.thread_id})")
        if not state.next:
            return dict(state.values)
        if cp.pause is not None and cp.pause(nodes, tuple(state.next)):
            raise RunPaused(cp.thread_id, nodes, tuple(state.next))
