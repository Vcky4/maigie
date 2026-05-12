import asyncio

from prisma import Prisma


async def main():
    db = Prisma()
    try:
        await db.connect()
        print("Connected successfully")
        await db.disconnect()
    except Exception as e:
        print(f"Failed to connect: {e}")


if __name__ == "__main__":
    asyncio.run(main())
