"""
:class:`AdpAgentHost` — the entry point adopters instantiate to run an
ADP agent on the Python / FastAPI runtime.

Mirrors the TypeScript ``AdpAgent`` class and the C# ``AdpAgentHost``
class: one constructor, a ``start`` coroutine, ``before_stop`` / ``after_start``
lifecycle hooks, and an ``app`` property for adopters who want to mount
custom routes on top of the built :class:`fastapi.FastAPI` instance.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import FastAPI

from .config import AgentConfig, JournalBackend
from .deliberation import RuntimeDeliberation
from .evaluator import Evaluator, ShellEvaluator, StaticEvaluator
from .llm_evaluator import LlmEvaluator
from .journal import JsonlJournalStore, RuntimeJournalStore, SqliteJournalStore
from .middleware import AuthMiddleware, RateLimitMiddleware
from .routing import register_routes


LifecycleHook = Callable[[], Awaitable[None]]


class AdpAgentHost:
    """
    Minimal usage::

        config = AgentConfig(
            agent_id="did:adp:my-agent-v1",
            port=3000,
            ...
        )
        host = AdpAgentHost(config)
        await host.run()

    Advanced usage: pass a custom :class:`Evaluator` or
    :class:`RuntimeJournalStore`, and register lifecycle hooks via
    :meth:`after_start` and :meth:`before_stop`.
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        journal: RuntimeJournalStore | None = None,
        evaluator: Evaluator | None = None,
    ) -> None:
        self._config = config
        self._journal: RuntimeJournalStore = journal or self._build_default_journal(config)
        self._evaluator: Evaluator = evaluator or self._build_default_evaluator(config)
        self._runtime = RuntimeDeliberation(config, self._journal, self._evaluator)
        self._after_start: list[LifecycleHook] = []
        self._before_stop: list[LifecycleHook] = []
        self._started = False

        self._app = FastAPI(
            title=f"adp-agent:{config.agent_id}",
            version="0.1.0",
        )

        # Middleware (applied in reverse order of addition in Starlette — auth
        # runs first, then rate limit, which matches the C# and TS runtimes'
        # pipeline order).
        self._app.add_middleware(RateLimitMiddleware)
        self._app.add_middleware(AuthMiddleware, config=config)

        register_routes(self._app, config, self._journal, self._runtime)

    # ---------- public surface ----------

    @property
    def app(self) -> FastAPI:
        """The underlying FastAPI application. Mount additional routes on it before calling :meth:`start`."""
        return self._app

    @property
    def journal(self) -> RuntimeJournalStore:
        return self._journal

    @property
    def config(self) -> AgentConfig:
        return self._config

    def after_start(self, hook: LifecycleHook) -> AdpAgentHost:
        """Register a callback to run after the host has started listening."""
        self._after_start.append(hook)
        return self

    def before_stop(self, hook: LifecycleHook) -> AdpAgentHost:
        """Register a callback to run before the host shuts down."""
        self._before_stop.append(hook)
        return self

    async def start(self) -> None:
        """
        Start the uvicorn server in the background and return once it's
        listening. Lifecycle hooks fire after the server is up.
        """
        if self._started:
            raise RuntimeError("AdpAgentHost already started")
        self._started = True

        import uvicorn
        config = uvicorn.Config(
            self._app,
            host="0.0.0.0",
            port=self._config.port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

        # Wait for the server to be ready.
        while not self._server.started:
            await asyncio.sleep(0.05)

        print(f"[{self._config.agent_id}] listening on :{self._config.port}")
        print(f"  manifest:    http://localhost:{self._config.port}/.well-known/adp-manifest.json")
        print(f"  calibration: http://localhost:{self._config.port}/.well-known/adp-calibration.json")
        print(f"  journal:     http://localhost:{self._config.port}/adj/v0/")

        for hook in self._after_start:
            await hook()

    async def stop(self) -> None:
        """Gracefully stop the host. Runs before-stop hooks first."""
        for hook in self._before_stop:
            try:
                await hook()
            except Exception as ex:
                print(f"[AdpAgentHost] before-stop hook failed: {ex}")

        if hasattr(self, "_server"):
            self._server.should_exit = True
            try:
                await asyncio.wait_for(self._server_task, timeout=10)
            except asyncio.TimeoutError:
                pass

        if hasattr(self._journal, "close"):
            close = getattr(self._journal, "close")
            if callable(close):
                close()

    async def run(self) -> None:
        """Convenience: start, wait for shutdown signal, stop."""
        await self.start()
        if hasattr(self, "_server_task"):
            try:
                await self._server_task
            finally:
                await self.stop()

    # ---------- defaults ----------

    @staticmethod
    def _build_default_journal(config: AgentConfig) -> RuntimeJournalStore:
        if config.journal_backend == JournalBackend.SQLITE:
            return SqliteJournalStore(config.journal_dir)
        return JsonlJournalStore(config.journal_dir)

    @staticmethod
    def _build_default_evaluator(config: AgentConfig) -> Evaluator:
        if config.evaluator is None:
            return StaticEvaluator(config)
        if config.evaluator.kind == "shell":
            return ShellEvaluator(config)
        if config.evaluator.kind == "static":
            return StaticEvaluator(config)
        if config.evaluator.kind == "llm":
            return LlmEvaluator(config)
        raise ValueError(
            f"Unknown evaluator kind '{config.evaluator.kind}'. "
            "Pass a custom Evaluator to AdpAgentHost(..., evaluator=...) instead."
        )


__all__ = ["AdpAgentHost"]
