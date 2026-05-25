from langgraph.checkpoint.memory import MemorySaver


def initialize_postgres_saver():
    """Returns an in-memory checkpoint saver.

    Name kept for backwards compatibility with existing call sites.
    Agent runs are short, single-process, and not resumed across restarts,
    so persistence isn't needed. See plan: switch_to_memorysaver.
    """
    return MemorySaver()
