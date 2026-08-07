"""pytest bootstrap — guarantees memory env before any app import.

``app.config.settings`` is instantiated at first import of ``app.main``, so
whichever test module imports it first bakes the values for the whole
process. ``test_memory.py`` sets ``MEMORY_DIR`` itself, but in smoke-first
ordering it would be imported too late. Setting it here (before any test
module import) keeps the memory store in a temp dir in every ordering.
"""

import os
import tempfile

os.environ.setdefault(
    "MEMORY_DIR",
    os.path.join(tempfile.mkdtemp(prefix="llmdash_mem_test_"), "memory"),
)
