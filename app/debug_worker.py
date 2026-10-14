"""Interactive local learning worker: python -m app.debug_worker.

Stop normal workers and Beat before using this on queued test jobs.
No email bodies or credentials are printed automatically.
"""
import ast
import inspect
import pdb

from app import tasks
from app.celery_app import celery


def main():
    filename = inspect.getsourcefile(tasks)
    tree = ast.parse(inspect.getsource(tasks))
    debugger = pdb.Pdb()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"dispatch_due", "deliver"}:
            debugger.set_break(filename, node.body[0].lineno)
    print("Type c to start. Breakpoints are set at dispatch_due and deliver.")
    print("Use n = next line, p expression = inspect, c = continue, q = quit.")
    debugger.runcall(celery.worker_main, [
        "worker", "--pool=solo", "--concurrency=1", "--loglevel=info",
        "--hostname=debug@%h", "--without-mingle", "--without-gossip",
    ])


if __name__ == "__main__":
    main()
