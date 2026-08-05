"""Wake offline hosts that live on cloud compute the server can power on.

An *external* host is normally something the server can only wait for — a
laptop is offline until its owner opens it. But a host that runs on cloud
compute the server has permission to start is different: the picker can offer
to wake it instead of dead-ending on "OFFLINE".

This is deliberately **config-only and opt-in**. With no ``host_wake:`` block
the module resolves nothing, ``GET /v1/hosts`` is byte-identical to before,
and the picker behaves exactly as it always has — so a laptop-only install is
completely unaffected. A host becomes wakeable only when an operator names it
alongside an explicit instance id.

Why not reuse ``sandbox_provider``: that field means "server-managed sandbox",
and clients use it to HIDE hosts from manual pickers. Tagging a wakeable host
with it would remove it from the very dropdown this feature targets.

Server config::

    host_wake:
      - host_name: omnigent-devbox
        provider: ec2
        instance_id: i-099d66548b496d876
        region: us-east-1

Credentials are never in this file (12-factor): the ``ec2`` provider uses the
server's ambient AWS credential chain — on EC2 that is the instance role, so
nothing is stored anywhere.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

# Providers that can power a host on. Parsing accepts only these so an
# operator typo fails at startup rather than as a mystery 500 on first click.
SUPPORTED_WAKE_PROVIDERS: frozenset[str] = frozenset({"ec2"})


@dataclass(frozen=True)
class HostWakeTarget:
    """
    One host the server is allowed to power on.

    :param host_name: The host's registered name, e.g. ``"omnigent-devbox"``.
        Matched against ``hosts.name`` — a name, not an id, because the id is
        minted at first registration and would change on a rebuild, while the
        name is stable and is what an operator actually knows.
    :param provider: Compute provider, e.g. ``"ec2"``.
    :param instance_id: Provider instance identifier, e.g.
        ``"i-099d66548b496d876"``.
    :param region: Provider region, e.g. ``"us-east-1"``.
    """

    host_name: str
    provider: str
    instance_id: str
    region: str


def parse_host_wake_config(raw: object) -> dict[str, HostWakeTarget]:
    """
    Parse the server config's ``host_wake:`` section.

    Fails loud on malformed config: an operator typo should stop startup, not
    surface later as a wake button that silently does nothing.

    :param raw: The raw ``host_wake`` value from the server config YAML, or
        ``None`` when the section is absent.
    :returns: Mapping of host name → target. Empty when unconfigured, which
        disables the feature entirely.
    :raises ValueError: When the section is present but malformed.
    """
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise ValueError("server config 'host_wake' must be a list of host entries")
    targets: dict[str, HostWakeTarget] = {}
    for index, entry in enumerate(raw):
        where = f"server config 'host_wake[{index}]'"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be a mapping")
        unknown = sorted(set(entry) - {"host_name", "provider", "instance_id", "region"})
        if unknown:
            raise ValueError(f"{where} has unknown key(s): {', '.join(unknown)}")
        host_name = entry.get("host_name")
        if not isinstance(host_name, str) or not host_name.strip():
            raise ValueError(f"{where}.host_name is required (the registered host name)")
        provider = entry.get("provider")
        if provider not in SUPPORTED_WAKE_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_WAKE_PROVIDERS))
            raise ValueError(f"{where}.provider must be one of: {supported} (got {provider!r})")
        instance_id = entry.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError(f"{where}.instance_id is required")
        region = entry.get("region")
        if not isinstance(region, str) or not region.strip():
            raise ValueError(f"{where}.region is required, e.g. 'us-east-1'")
        name = host_name.strip()
        if name in targets:
            raise ValueError(f"{where}.host_name '{name}' is configured more than once")
        targets[name] = HostWakeTarget(
            host_name=name,
            provider=provider,
            instance_id=instance_id.strip(),
            region=region.strip(),
        )
    return targets


class HostWakeError(Exception):
    """A wake attempt failed; the message is safe to surface to the caller."""


def _start_ec2_sync(target: HostWakeTarget) -> str:
    """
    Start an EC2 instance, returning its state after the call.

    Idempotent: an already-running instance is success, since the desired end
    state holds. Blocking (boto3 is sync) — callers use :func:`wake_host`.

    :param target: The configured wake target.
    :returns: The instance state, e.g. ``"pending"`` or ``"running"``.
    :raises HostWakeError: When boto3 is unavailable or the API call fails.
    """
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise HostWakeError(
            "the 'ec2' host-wake provider needs boto3 — install with `pip install 'omnigent[ec2]'`"
        ) from exc

    client = boto3.client("ec2", region_name=target.region)
    try:
        described = client.describe_instances(InstanceIds=[target.instance_id])
        state = described["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state == "running":
            return state
        # "stopping" cannot be started until it settles; say so plainly
        # rather than letting boto3 raise IncorrectInstanceState.
        if state == "stopping":
            raise HostWakeError(
                f"instance {target.instance_id} is still stopping; retry in a few seconds"
            )
        client.start_instances(InstanceIds=[target.instance_id])
        return "pending"
    except HostWakeError:
        raise
    except Exception as exc:
        # botocore raises many shapes (auth, throttling, wrong state); all of
        # them must reach the caller as one clear reason, not a 500.
        raise HostWakeError(f"could not start {target.instance_id}: {exc}") from exc


async def wake_host(target: HostWakeTarget) -> str:
    """
    Power on the compute behind a wakeable host.

    Only starts the machine. The host process re-registers itself on boot (a
    supervised service), so the caller polls host liveness rather than waiting
    here — a wake takes ~40-60s and must not hold a request open.

    :param target: The configured wake target.
    :returns: Provider state after the call, e.g. ``"pending"``.
    :raises HostWakeError: When the provider rejects the start.
    """
    _logger.info(
        "Waking host %s (%s %s in %s)",
        target.host_name,
        target.provider,
        target.instance_id,
        target.region,
    )
    return await asyncio.to_thread(_start_ec2_sync, target)
