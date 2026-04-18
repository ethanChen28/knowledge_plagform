#!/usr/bin/env python3
"""Test Neo4j Bolt connection from inside container."""
from neo4j import AsyncGraphDatabase
import asyncio

async def test_connection():
    try:
        # Test with neo4j URI scheme (routing)
        print("Testing neo4j:// scheme...")
        driver = AsyncGraphDatabase.driver(
            "neo4j://host.docker.internal:7687",
            auth=("neo4j", "12345678")
        )
        async with driver.session() as session:
            result = await session.run("RETURN 1 as num")
            data = await result.data()
            print(f"✓ neo4j:// success: {data}")
        await driver.close()
    except Exception as e:
        print(f"✗ neo4j:// failed: {e.__class__.__name__}: {str(e)}")

    print("\nTesting bolt:// scheme...")
    try:
        # Test with bolt URI scheme (direct)
        driver = AsyncGraphDatabase.driver(
            "bolt://host.docker.internal:7687",
            auth=("neo4j", "12345678")
        )
        async with driver.session() as session:
            result = await session.run("RETURN 1 as num")
            data = await result.data()
            print(f"✓ bolt:// success: {data}")
        await driver.close()
    except Exception as e:
        print(f"✗ bolt:// failed: {e.__class__.__name__}: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_connection())
