import threading
from PySide6.QtCore import QThread, Signal

class JobWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, task):
        super().__init__()
        self.task = task
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.succeeded.emit(self.task(self.cancel_event))
        except Exception as error:
            self.failed.emit(str(error))

    def cancel(self):
        self.cancel_event.set()
