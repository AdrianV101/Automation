"""Minimal in-process IMAP fake for testing ImapBridge.

Not a full IMAP server — just enough surface for LOGIN, SELECT INBOX,
UID SEARCH, UID FETCH, IDLE, DONE. Uses a plain TCP listener on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class FakeMessage:
    uid: int
    raw: bytes


@dataclass
class FakeImapServer:
    port: int = 0
    username: str = "user@x"
    password: str = "pw"
    messages: list[FakeMessage] = field(default_factory=list)
    reject_login: bool = False
    selected_folders: list[str] = field(default_factory=list)
    _server: asyncio.base_events.Server | None = None
    _idle_writers: list[asyncio.StreamWriter] = field(default_factory=list)

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self.port,
        )
        socket = self._server.sockets[0]
        self.port = socket.getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def push_new_message(self, msg: FakeMessage) -> None:
        self.messages.append(msg)
        for w in list(self._idle_writers):
            try:
                w.write(f"* {len(self.messages)} EXISTS\r\n".encode())
                await w.drain()
            except ConnectionError:
                self._idle_writers.remove(w)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            writer.write(b"* OK [CAPABILITY IMAP4rev1 IDLE] FakeIMAP ready\r\n")
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    tag, cmd, *args = line.decode().strip().split(" ", 2)
                except ValueError:
                    continue
                cmd = cmd.upper()
                if cmd == "CAPABILITY":
                    writer.write(
                        b"* CAPABILITY IMAP4rev1 IDLE\r\n"
                        + f"{tag} OK CAPABILITY completed\r\n".encode(),
                    )
                elif cmd == "LOGIN":
                    if self.reject_login:
                        writer.write(f"{tag} NO [AUTHENTICATIONFAILED] invalid\r\n".encode())
                    else:
                        creds = args[0] if args else ""
                        u = creds.split(" ")[0].strip('"')
                        if u != self.username:
                            writer.write(
                                f"{tag} NO [AUTHENTICATIONFAILED] invalid\r\n".encode(),
                            )
                        else:
                            writer.write(f"{tag} OK LOGIN completed\r\n".encode())
                elif cmd == "SELECT":
                    folder = (args[0] if args else "").strip().strip('"')
                    self.selected_folders.append(folder)
                    writer.write(
                        f"* {len(self.messages)} EXISTS\r\n"
                        f"* OK [UIDNEXT {len(self.messages) + 1}] Predicted\r\n"
                        f"{tag} OK [READ-WRITE] SELECT completed\r\n".encode(),
                    )
                elif cmd == "UID" and args and args[0].upper().startswith("FETCH"):
                    parts = args[0].split()
                    uid = int(parts[1])
                    msg = next((m for m in self.messages if m.uid == uid), None)
                    if msg:
                        writer.write(
                            f"* 1 FETCH (UID {uid} RFC822 {{{len(msg.raw)}}}\r\n".encode()
                            + msg.raw
                            + b")\r\n"
                            + f"{tag} OK FETCH completed\r\n".encode(),
                        )
                    else:
                        writer.write(f"{tag} NO no such UID\r\n".encode())
                elif cmd == "IDLE":
                    writer.write(b"+ idling\r\n")
                    await writer.drain()
                    self._idle_writers.append(writer)
                    done = await reader.readline()
                    if done.strip().upper() == b"DONE":
                        self._idle_writers.remove(writer)
                        writer.write(f"{tag} OK IDLE terminated\r\n".encode())
                elif cmd == "LOGOUT":
                    writer.write(f"* BYE\r\n{tag} OK LOGOUT\r\n".encode())
                    await writer.drain()
                    return
                else:
                    writer.write(f"{tag} OK {cmd} completed\r\n".encode())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if writer in self._idle_writers:
                self._idle_writers.remove(writer)
