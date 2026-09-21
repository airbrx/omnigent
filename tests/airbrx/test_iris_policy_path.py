"""
The spec's policy function path must be importable in a runner process.

This exists because it was not. On the execution host the spec named
`iris.policy.tool_boundary`, and the runner's resolver reported:

    runner policy failed to resolve (ModuleNotFoundError); function path
    'iris.policy.tool_boundary' could not be loaded; tool calls are denied
    until this policy is fixed

`iris` only reaches sys.path through `source_root()`, which the runner never
calls. The re-export in `omnigent.airbrx.iris.policy` is part of Omnigent, so
it imports anywhere Omnigent runs.
"""

import importlib

import pytest

# The exact string the Iris agent spec carries in guardrails.policies.
SPEC_FUNCTION_PATH = "omnigent.airbrx.iris.policy.tool_boundary"


def _resolve(path: str):
    """Resolve 'module.attr' the way the runner's policy loader does."""
    module_path, _, attr = path.rpartition(".")
    return getattr(importlib.import_module(module_path), attr)


def test_the_spec_function_path_resolves():
    # Resolution must not require the vendored package to be extracted first;
    # the runner resolves every spec policy at startup, before any turn.
    assert callable(_resolve(SPEC_FUNCTION_PATH))


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("mcp__omnigent__iris_overview", "ALLOW"),
        ("mcp__omnigent__iris_investigate", "ALLOW"),
        ("mcp__omnigent__iris_audit", "ALLOW"),
        ("mcp__omnigent__iris_propose", "ALLOW"),
        # Discovery, not execution — iris #22 allows it so that enforcing the
        # boundary cannot break Iris finding her own tools.
        ("ToolSearch", "ALLOW"),
        ("Bash", "DENY"),
        # Loading a skill is execution surface, not discovery.
        ("Skill", "DENY"),
        ("WebFetch", "DENY"),
    ],
)
def test_the_re_export_returns_the_vendored_verdicts(target, expected):
    # Asserts the delegation, not the policy: the verdicts themselves are the
    # iris repository's to define and test. If these drift, the re-export has
    # started disagreeing with the boundary it is supposed to forward to.
    verdict = _resolve(SPEC_FUNCTION_PATH)({"type": "tool_call", "target": target})
    assert verdict["result"] == expected


def test_a_non_tool_call_event_is_not_a_denial():
    verdict = _resolve(SPEC_FUNCTION_PATH)({"type": "prompt"})
    assert verdict["result"] == "ALLOW"
