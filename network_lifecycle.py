"""Keep application HTTP work alive until it is safe to tear down OpenSSL."""

import importlib
import threading
import requests as _requests  # Static import keeps the HTTP dependency in frozen builds.


class ShutdownRequests:
    """Requests facade with an admission gate and in-flight request accounting.

    No process-wide monkeypatch: only SingWS's HTTP calls use this instance.
    Shutdown rejects new work, then drains existing TLS users before Qt exits.
    Streaming responses remain counted until their context/response closes.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._closing = False
        self._active = 0

    def __getattr__(self, name):
        module = importlib.import_module(_requests.__name__)
        if name not in {"get", "post", "put", "patch", "delete", "head", "options", "request"}:
            return getattr(module, name)

        def call(*args, **kwargs):
            with self._condition:
                if self._closing:
                    raise module.exceptions.ConnectionError("SingWS is shutting down")
                self._active += 1
            streaming = False
            try:
                response = getattr(module, name)(*args, **kwargs)
                if kwargs.get("stream", False):
                    streaming = True
                    return _StreamingResponse(response, self._finished)
                return response
            finally:
                if not streaming:
                    self._finished()
        return call

    def _finished(self):
        with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def begin_shutdown(self):
        with self._condition:
            self._closing = True

    def wait_for_idle(self):
        # Never kill a thread inside OpenSSL or let process teardown race it.
        # Callers retain their existing connect/read timeouts. This wait is
        # confined to application exit, never used by the playback/UI loop.
        with self._condition:
            self._condition.wait_for(lambda: self._active == 0)


class _StreamingResponse:
    def __init__(self, response, finished):
        self._response = response
        self._finished = finished
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._response, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._lock:
            if self._finished is not None:
                try:
                    self._response.close()
                finally:
                    self._finished()
                    self._finished = None
