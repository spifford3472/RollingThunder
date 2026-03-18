from __future__ import annotations

import socket
import threading
from dataclasses import dataclass


class HamlibError(Exception):
    pass


class RigctldUnreachable(HamlibError):
    pass


class RigctldProtocolError(HamlibError):
    pass


class RigctldCommandError(HamlibError):
    def __init__(self, code: int, command: str, response: str):
        super().__init__(f"rigctld command failed: code={code} command={command!r}")
        self.code = code
        self.command = command
        self.response = response


@dataclass
class ModeReadback:
    mode: str
    passband_hz: int


class HamlibClient:
    def __init__(self, host: str, port: int, timeout_sec: float = 2.0):
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _connect(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout_sec)
            sock.connect((self.host, self.port))
            self._sock = sock
        except OSError as exc:
            self._sock = None
            raise RigctldUnreachable(
                f"unable to contact rigctld at {self.host}:{self.port}"
            ) from exc

    def _ensure_connected(self) -> None:
        if self._sock is None:
            self._connect()

    def _send(self, cmd: str) -> None:
        self._ensure_connected()
        assert self._sock is not None
        payload = (cmd.strip() + "\n").encode("utf-8")
        try:
            self._sock.sendall(payload)
        except OSError:
            self.close()
            self._ensure_connected()
            assert self._sock is not None
            self._sock.sendall(payload)

    def _recv_until_rprt(self) -> str:
        """
        For set/action commands, rigctld replies with one or more lines ending in:
            RPRT <code>
        """
        assert self._sock is not None
        chunks: list[bytes] = []
        while True:
            try:
                data = self._sock.recv(4096)
            except socket.timeout as exc:
                self.close()
                raise RigctldProtocolError("timed out waiting for RPRT response from rigctld") from exc
            except OSError as exc:
                self.close()
                raise RigctldUnreachable("connection to rigctld dropped") from exc

            if not data:
                self.close()
                raise RigctldUnreachable("rigctld closed connection")

            chunks.append(data)
            joined = b"".join(chunks)
            if b"RPRT " in joined:
                return joined.decode("utf-8", errors="replace").strip()

    def _recv_until_quiet(self) -> str:
        """
        For query/read commands, rigctld often returns payload lines without an RPRT line.
        Read until the socket goes quiet after at least one chunk arrives.
        """
        assert self._sock is not None
        chunks: list[bytes] = []
        received_any = False

        while True:
            try:
                data = self._sock.recv(4096)
            except socket.timeout:
                if received_any:
                    break
                self.close()
                raise RigctldProtocolError("timed out waiting for payload response from rigctld")
            except OSError as exc:
                self.close()
                raise RigctldUnreachable("connection to rigctld dropped") from exc

            if not data:
                if received_any:
                    break
                self.close()
                raise RigctldUnreachable("rigctld closed connection")

            chunks.append(data)
            received_any = True

            # Common cases return in a single recv; keep reading until timeout
            # so multi-line query responses like "m" are handled cleanly.

        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    @staticmethod
    def _parse_rprt(response: str) -> int:
        for line in reversed(response.splitlines()):
            if line.startswith("RPRT "):
                try:
                    return int(line.split()[1])
                except (IndexError, ValueError) as exc:
                    raise RigctldProtocolError(f"malformed RPRT line: {line!r}") from exc
        raise RigctldProtocolError(f"no RPRT line in response: {response!r}")

    @staticmethod
    def _payload_lines(response: str) -> list[str]:
        return [
            line.strip()
            for line in response.splitlines()
            if line.strip() and not line.startswith("RPRT ")
        ]

    def command(self, cmd: str) -> str:
        """
        For set/action commands that should end with RPRT.
        """
        with self._lock:
            self._send(cmd)
            response = self._recv_until_rprt()
            rc = self._parse_rprt(response)
            if rc != 0:
                raise RigctldCommandError(code=rc, command=cmd, response=response)
            return response

    def query(self, cmd: str) -> str:
        """
        For read/query commands that return payload only.
        """
        with self._lock:
            self._send(cmd)
            return self._recv_until_quiet()

    def get_freq(self) -> int:
        response = self.query("f")
        lines = self._payload_lines(response)
        if not lines:
            raise RigctldProtocolError("empty get_freq response")
        return int(lines[0])

    def set_freq(self, freq_hz: int) -> None:
        self.command(f"F {int(freq_hz)}")

    def get_mode(self) -> ModeReadback:
        response = self.query("m")
        lines = self._payload_lines(response)
        if len(lines) < 2:
            raise RigctldProtocolError(f"unexpected get_mode response: {response!r}")
        return ModeReadback(mode=lines[0].upper(), passband_hz=int(lines[1]))

    def set_mode(self, mode: str, passband_hz: int) -> None:
        self.command(f"M {mode.upper()} {int(passband_hz)}")

    def start_tuner(self) -> None:
        self.command("U TUNER 1")

    def get_tuner_state(self) -> str:
        response = self.query("u TUNER")
        lines = self._payload_lines(response)
        return lines[0] if lines else ""

    def raw_cat(self, cat_command: str, expected_bytes: int = 16) -> str:
        """
        Send a raw CAT command through rigctld's 'w' passthrough.

        expected_bytes is the number of bytes rigctld should read back from
        the radio before returning — NOT a timeout. Pass the exact or slightly
        generous byte count for the response you expect.
        """
        with self._lock:
            self._send(f"w {cat_command} {int(expected_bytes)}")
            return self._recv_until_quiet()