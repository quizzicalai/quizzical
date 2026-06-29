"""Q&A icon enrichment (DRAFT — behind ``quizzical.images.qa_icons_enabled``).

This package productionizes the validated Q&A → brand-icon routing prototype
(``prototype/qa-image-enrichment``). It is gated by the
``quizzical.images.qa_icons_enabled`` flag, which is **OFF by default**.

Lazy-import contract (the #1 hard requirement):
  - This ``__init__`` deliberately imports NOTHING heavy. In particular it does
    NOT import ``embedder`` (which pulls in ``fastembed`` and loads a CPU model).
  - The only flag-gated entry point is ``hook.maybe_bind_icons``; it returns
    immediately when the flag is off, BEFORE importing ``embedder`` / ``binder``.
  - So when the flag is off, ``fastembed`` need not even be installed and no
    model is ever loaded.
"""

__all__: list[str] = []
