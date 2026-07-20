#!/usr/bin/env python3
"""Quick utility to fire a one-shot event on the NATS bus."""

import asyncio
import json
import sys

import nats


NATS_URL = "nats://localhost:4222"
SUBJECT = "limbic.proactive.trigger"


async def send(subject: str, payload: dict | None = None):
    nc = await nats.connect(NATS_URL)
    data = json.dumps(payload or {}).encode()
    await nc.publish(subject, data)
    await nc.flush()
    print(f"✉️  Published on '{subject}' → {payload or {}}")
    await nc.drain()


if __name__ == "__main__":
    subject = sys.argv[1] if len(sys.argv) > 1 else SUBJECT
    asyncio.run(send(subject))
