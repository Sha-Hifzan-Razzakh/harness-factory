"""Framework runners (RUN, docs/adr/0016-runner-axis.md): the same recipe executed by a framework's own agent.

`identity` is import-light (the recipe hash reads it); the runner modules import their framework only when a run
uses them, so native runs never load LangChain or LlamaIndex.
"""
