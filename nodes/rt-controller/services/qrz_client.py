from __future__ import annotations

import os
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Mapping


QRZ_XML_URL = "https://xmldata.qrz.com/xml/current/"
QRZ_XML_NS = {"qrz": "http://xmldata.qrz.com"}


def _first_text(parent: ET.Element | None, tag: str) -> str:
    if parent is None:
        return ""
    node = parent.find(tag, QRZ_XML_NS)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


class QRZClient:
    """
    Minimal QRZ XML client for callsign lookup.

    Responsibilities:
      - login and obtain session key
      - reuse session key
      - re-login once when session is invalid
      - return a small raw mapping for caller normalization
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url: str = QRZ_XML_URL,
        timeout_sec: float = 6.0,
    ) -> None:
        self._username = str(username or "").strip()
        self._password = str(password or "").strip()
        self._base_url = base_url
        self._timeout_sec = float(timeout_sec)
        self._session_key = ""
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "QRZClient":
        return cls(
            username=os.environ.get("RT_QRZ_USERNAME", ""),
            password=os.environ.get("RT_QRZ_PASSWORD", ""),
            timeout_sec=float(os.environ.get("RT_QRZ_TIMEOUT_SEC", "6.0")),
        )

    def _fetch_xml(self, params: Mapping[str, Any]) -> ET.Element:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        url = f"{self._base_url}?{query}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RollingThunder/0.3800 (+https://rollingthunder.local)"
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:
            data = resp.read()
        return ET.fromstring(data)

    def _parse_session(self, root: ET.Element) -> tuple[str, str]:
        session = root.find("qrz:Session", QRZ_XML_NS)
        key = _first_text(session, "qrz:Key")
        error = _first_text(session, "qrz:Error")
        return key, error

    def _login_locked(self) -> str:
        if not self._username or not self._password:
            raise RuntimeError("QRZ credentials are not configured")

        root = self._fetch_xml(
            {
                "username": self._username,
                "password": self._password,
            }
        )
        key, error = self._parse_session(root)
        if not key:
            raise RuntimeError(f"QRZ login failed: {error or 'missing session key'}")
        self._session_key = key
        return key

    def _get_session_key(self) -> str:
        with self._lock:
            if self._session_key:
                return self._session_key
            return self._login_locked()

    def _query_once(self, call: str) -> Mapping[str, Any] | None:
        key = self._get_session_key()
        root = self._fetch_xml(
            {
                "s": key,
                "callsign": call,
            }
        )

        session_key, session_error = self._parse_session(root)
        if session_key:
            with self._lock:
                self._session_key = session_key

        callsign = root.find("qrz:Callsign", QRZ_XML_NS)
        if callsign is None:
            if session_error:
                return {"_session_error": session_error}
            return None

        fname = _first_text(callsign, "qrz:fname")
        name = _first_text(callsign, "qrz:name")
        full_name = " ".join([x for x in [fname, name] if x]).strip()

        return {
            "callsign": call,
            "name": full_name or fname or name,
            "fname": fname,
            "lname": name,
            "addr1": _first_text(callsign, "qrz:addr1"),
            "addr2": _first_text(callsign, "qrz:addr2"),
            "state": _first_text(callsign, "qrz:state"),
            "zip": _first_text(callsign, "qrz:zip"),
            "country": _first_text(callsign, "qrz:country"),
            "grid": _first_text(callsign, "qrz:grid"),
            "lat": _first_text(callsign, "qrz:lat"),
            "lon": _first_text(callsign, "qrz:lon"),
            "image": _first_text(callsign, "qrz:image"),
            "class": _first_text(callsign, "qrz:class"),
            "codes": _first_text(callsign, "qrz:codes"),
            "qslmgr": _first_text(callsign, "qrz:qslmgr"),
            "email": _first_text(callsign, "qrz:email"),
            "url": _first_text(callsign, "qrz:url"),
        }

    def lookup_callsign(self, call: str) -> Mapping[str, Any] | None:
        normalized_call = str(call or "").strip().upper()
        if not normalized_call:
            return None

        result = self._query_once(normalized_call)
        if result and "_session_error" not in result:
            return result

        with self._lock:
            self._session_key = ""
            self._login_locked()

        result = self._query_once(normalized_call)
        if result and "_session_error" not in result:
            return result

        return None


def qrz_fetch_callsign(call: str) -> Mapping[str, Any] | None:
    client = QRZClient.from_env()
    return client.lookup_callsign(call)