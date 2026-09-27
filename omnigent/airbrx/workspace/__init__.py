"""The airbrx workspace kernel: plain browser scripts shared by agent workspaces.

The files live in ``ui/`` and are served by each agent's asset route through
:func:`omnigent.airbrx.workspace.assets.kernel_asset`. Nothing here imports an
agent package; the kernel is agent-agnostic.
"""
