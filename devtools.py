#!/usr/bin/env python3
import asyncio
import os
import random
import sys
import aiohttp
from aiohttp import web
from aiohttp.web import middleware
import websockets
import sys


if len(sys.argv) == 1:
    print("No port specified, setting up wireless ADB on a random port using root")
    port = random.randint(10000, 60000)
    command = f"""
su -c '
setprop service.adb.tcp.port {port} &&
stop adbd &&
start adbd
'
"""
    ret = os.system(command)
    if ret != 0:
        print("[!] Failed to set up wireless ADB, exiting")
        sys.exit(1)
    print(f"Wireless ADB set up successfully on port {port}")
elif sys.argv[1].isdigit():
    port = int(sys.argv[1])
    print(f"Connecting to an existing wireless ADB on port {port}")
else:
    print("Usage:")
    print(f"{sys.argv[0]}         (Use root to set up wireless ADB on a random port)")
    print(f"{sys.argv[0]} 12345   (Connect to an existing wireless ADB on port 12345)")
    sys.exit(1)


os.system("adb disconnect")
ret = os.system(f"adb connect localhost:{port}")
if ret != 0:
    print("[!] Failed to connect to wireless ADB")
    sys.exit(1)
ret = os.system("adb forward tcp:9222 localabstract:chrome_devtools_remote")
if ret != 0:
    print("[!] Failed to forward CDP port")
    sys.exit(1)


# Prompt the user for a Tab to open DevTools for
async def open_tab():
    async def ainput() -> str:
        return await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://localhost:9222/json") as resp:
                    tabs = await resp.json()
        except Exception as e:
            print(f"[!] Failed to fetch tabs: {e}, Is your Chrome running?", flush=True)
            sys.exit(-1)

        print("Current tabs:")
        for i, tab in enumerate(tabs):
            color = "\033[31m" if i % 2 == 0 else "\033[32m"
            print(f"{color}[{tab['id']}] {tab['title']}\033[0m")

        print("Enter tab ID or Ctrl-C to exit: ", end="", flush=True)
        user_input = (await ainput()).strip()
        if not user_input.isdigit():
            print("Invalid input, try again", flush=True)
            continue
        print("Opening DevTools for tab", user_input.strip(), flush=True)
        os.system(
            f"termux-open-url https://chrome-devtools-frontend.appspot.com/serve_internal_file/@f84901f7f0a12725375071f589e8d9fc61af1de3/devtools_app.html?ws=localhost:9223/{user_input}"
        )


# A reverse proxy that forwards messages between the Chrome DevTools frontend
# and the Chrome DevTools Protocol server (ws://localhost:9222) to add CORS headers
@middleware
async def cors_middleware(request, handler):
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = (
        "chrome-devtools-frontend.appspot.com"
    )
    return response


async def websocket_handler(request):
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    async with websockets.connect(
        f"ws://localhost:9222/devtools/page/{request.match_info['id']}", max_size=None
    ) as ws_client:

        async def forward_server_to_client():
            async for msg in ws_server:
                await ws_client.send(msg.data)

        async def forward_client_to_server():
            async for msg in ws_client:
                await ws_server.send_str(msg)

        # Run both tasks concurrently
        await asyncio.gather(forward_server_to_client(), forward_client_to_server())

    return ws_server


async def reverse_proxy():
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_route("GET", "/{id}", websocket_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 9223)
    await site.start()
    await asyncio.Event().wait()


loop = asyncio.new_event_loop()
loop.create_task(open_tab())
loop.create_task(reverse_proxy())
loop.run_forever()
