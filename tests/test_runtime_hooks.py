import asyncio

from brain.runtime_hooks import RuntimeHooks


def test_runtime_hooks_chain_sync_and_async() -> None:
    hooks = RuntimeHooks()

    def sync_hook(payload: dict):
        out = dict(payload)
        out["x"] = int(out.get("x", 0)) + 1
        return out

    async def async_hook(payload: dict):
        out = dict(payload)
        out["x"] = int(out.get("x", 0)) * 3
        return out

    hooks.register("before_model", sync_hook)
    hooks.register("before_model", async_hook)

    async def _run() -> None:
        out = await hooks.run("before_model", {"x": 2})
        assert out["x"] == 9

    asyncio.run(_run())


def test_runtime_hooks_uses_latest_declarative_version_per_org() -> None:
    hooks = RuntimeHooks()
    hooks.register_declarative(
        event="before_tool",
        mode="annotate",
        metadata={"tag": "v1"},
        organization_id="org-1",
        version=1,
    )
    hooks.register_declarative(
        event="before_tool",
        mode="annotate",
        metadata={"tag": "v2"},
        organization_id="org-1",
        version=2,
    )

    async def _run() -> None:
        out = await hooks.run("before_tool", {"client_id": "org-1"})
        annotations = out.get("annotations") or []
        assert len(annotations) == 1
        assert annotations[0]["version"] == 2
        assert annotations[0]["tag"] == "v2"

    asyncio.run(_run())
