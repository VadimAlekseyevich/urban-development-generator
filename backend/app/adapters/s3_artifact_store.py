from __future__ import annotations

import hashlib
import hmac
import re
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import BinaryIO, Final, cast
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    require_temporary_artifact_ref,
)

_METADATA_VERSION: Final = "1"
_DEFAULT_CHUNK_SIZE: Final = 1024 * 1024
_DEFAULT_SPOOL_LIMIT: Final = 8 * 1024 * 1024
_EMPTY_SHA256: Final = hashlib.sha256(b"").hexdigest()
_SPACE_RE: Final = re.compile(r"\s+")


class S3ArtifactStoreError(RuntimeError):
    """Raised when the configured S3-compatible service rejects an operation."""


class S3ArtifactStore:
    """S3/MinIO ArtifactStore adapter with storage-neutral logical refs.

    Payload objects live under ``<prefix>/<state>/<logical-key>``. Stable
    checksum/size/content-type presence metadata is stored as S3 user metadata,
    so no filesystem sidecar or database row is required to reconstruct
    ``ArtifactStat`` after a process restart.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        prefix: str = "urban-generator",
        force_path_style: bool = True,
        session_token: str | None = None,
        verify_tls: bool = True,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        spool_limit_bytes: int = _DEFAULT_SPOOL_LIMIT,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if spool_limit_bytes <= 0:
            raise ValueError("spool_limit_bytes must be positive")

        parsed = urlsplit(endpoint_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("endpoint_url must be an absolute http(s) URL")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("endpoint_url must not include a path, query, or fragment")
        if not bucket or "/" in bucket or bucket.strip() != bucket:
            raise ValueError("bucket must be a non-empty S3 bucket name")
        if not access_key.strip() or not secret_key.strip():
            raise ValueError("S3 credentials must be non-empty")
        if not region.strip():
            raise ValueError("region must be non-empty")

        normalized_prefix = prefix.strip("/")
        if normalized_prefix:
            segments = normalized_prefix.split("/")
            if any(segment in {"", ".", ".."} for segment in segments):
                raise ValueError("prefix must use non-empty logical path segments")
            if "\\" in normalized_prefix:
                raise ValueError("prefix must not contain backslashes")

        self._endpoint = parsed
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._prefix = normalized_prefix
        self._force_path_style = force_path_style
        self._session_token = session_token.strip() if session_token else None
        self._chunk_size = chunk_size
        self._spool_limit_bytes = spool_limit_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = client or httpx.Client(verify=verify_tls, timeout=60.0)
        self._gc_scan_lock = Lock()
        self._gc_scan_state_index = 0
        self._gc_start_after: dict[ArtifactState, str | None] = {
            ArtifactState.TEMPORARY: None,
            ArtifactState.READY: None,
        }

    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        require_temporary_artifact_ref(ref)
        ArtifactStat(
            ref=ref,
            size_bytes=0,
            checksum="sha256:" + "0" * 64,
            content_type=content_type,
        )

        if self._head(ref.as_ready()) is not None:
            raise ArtifactContractError("ready artifact already exists")

        with tempfile.SpooledTemporaryFile(
            max_size=self._spool_limit_bytes,
            mode="w+b",
        ) as staged:
            digest = hashlib.sha256()
            size_bytes = 0
            while True:
                chunk = source.read(self._chunk_size)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise TypeError("artifact source must yield bytes")
                staged.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            staged.seek(0)
            checksum = f"sha256:{digest.hexdigest()}"
            headers = {
                "content-length": str(size_bytes),
                "x-amz-meta-udg-version": _METADATA_VERSION,
                "x-amz-meta-udg-checksum": checksum,
                "x-amz-meta-udg-size": str(size_bytes),
                "x-amz-meta-udg-content-type-present": (
                    "1" if content_type is not None else "0"
                ),
            }
            if content_type is not None:
                headers["content-type"] = content_type
            response = self._request(
                "PUT",
                ref,
                headers=headers,
                content=cast(BinaryIO, staged),
                payload_hash=digest.hexdigest(),
            )
            self._raise_for_status(response, operation="put", ref=ref)

        return ArtifactStat(
            ref=ref,
            size_bytes=size_bytes,
            checksum=checksum,
            content_type=content_type,
        )

    def open(self, ref: ArtifactRef) -> BinaryIO:
        request = self._build_request("GET", ref, payload_hash=_EMPTY_SHA256)
        try:
            response = self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise S3ArtifactStoreError(f"S3 open failed for {ref.key}") from exc
        if response.status_code == 404:
            response.close()
            raise KeyError(ref)
        if response.status_code >= 400:
            try:
                self._raise_for_status(response, operation="open", ref=ref)
            finally:
                response.close()

        staged = tempfile.SpooledTemporaryFile(
            max_size=self._spool_limit_bytes,
            mode="w+b",
        )
        try:
            for chunk in response.iter_bytes(chunk_size=self._chunk_size):
                staged.write(chunk)
            staged.seek(0)
            return cast(BinaryIO, staged)
        except BaseException:
            staged.close()
            raise
        finally:
            response.close()

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        response = self._head(ref)
        if response is None:
            raise KeyError(ref)
        headers = response.headers
        if headers.get("x-amz-meta-udg-version") != _METADATA_VERSION:
            raise ArtifactContractError(
                f"unsupported artifact metadata version for {ref.key}"
            )

        checksum = headers.get("x-amz-meta-udg-checksum")
        raw_size = headers.get("x-amz-meta-udg-size")
        present = headers.get("x-amz-meta-udg-content-type-present")
        if raw_size is None or present not in {"0", "1"}:
            raise ArtifactContractError(f"artifact metadata is incomplete for {ref.key}")
        try:
            metadata_size = int(raw_size)
            actual_size = int(headers.get("content-length", ""))
        except ValueError as exc:
            raise ArtifactContractError(
                f"artifact size metadata is invalid for {ref.key}"
            ) from exc
        if metadata_size != actual_size:
            raise ArtifactContractError(
                f"artifact payload size does not match metadata for {ref.key}"
            )
        content_type = headers.get("content-type") if present == "1" else None
        return ArtifactStat(
            ref=ref,
            size_bytes=actual_size,
            checksum=checksum or "",
            content_type=content_type,
        )

    def delete(self, ref: ArtifactRef) -> None:
        response = self._request("DELETE", ref, payload_hash=_EMPTY_SHA256)
        if response.status_code == 404:
            return
        self._raise_for_status(response, operation="delete", ref=ref)

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        if ref.state is ArtifactState.READY:
            return self.stat(ref)
        require_temporary_artifact_ref(ref)

        ready_ref = ref.as_ready()
        temporary = self._stat_optional(ref)
        ready = self._stat_optional(ready_ref)
        if temporary is None:
            if ready is not None:
                return ready
            raise KeyError(ref)
        if ready is not None:
            self._require_same_artifact(temporary, ready)
            self.delete(ref)
            return ready

        copy_source = "/" + quote(
            f"{self._bucket}/{self._object_key(ref)}",
            safe="/-_.~",
        )
        response = self._request(
            "PUT",
            ready_ref,
            headers={
                "if-none-match": "*",
                "x-amz-copy-source": copy_source,
            },
            payload_hash=_EMPTY_SHA256,
        )
        if response.status_code in {409, 412}:
            ready = self._stat_optional(ready_ref)
            if ready is None:
                raise S3ArtifactStoreError(
                    "S3 promote conflicted before ready object became visible "
                    f"for {ref.key}"
                )
            self._require_same_artifact(temporary, ready)
        else:
            self._raise_for_status(response, operation="promote", ref=ref)
            ready = self.stat(ready_ref)
            self._require_same_artifact(temporary, ready)

        self.delete(ref)
        return ready

    def stale_run_refs(
        self,
        *,
        older_than: datetime,
        max_scan: int = 5000,
        max_results: int = 50,
    ) -> tuple[ArtifactRef, ...]:
        """Return a bounded rotating scan of stale S3 run-scoped objects."""
        if older_than.tzinfo is None or older_than.utcoffset() is None:
            raise ValueError("older_than must be timezone-aware")
        if not 1 <= max_results <= 500 or not 1 <= max_scan <= 100_000:
            raise ValueError("GC scan limits are out of bounds")

        candidates: list[ArtifactRef] = []
        seen: set[str] = set()
        scanned = 0
        states = (ArtifactState.TEMPORARY, ArtifactState.READY)
        with self._gc_scan_lock:
            visited_states = 0
            while (
                scanned < max_scan
                and len(candidates) < max_results
                and visited_states < 2
            ):
                state = states[self._gc_scan_state_index]
                page, is_truncated = self._list_run_objects(
                    state,
                    start_after=self._gc_start_after[state],
                    max_keys=min(1000, max_scan - scanned),
                )
                if not page:
                    self._gc_start_after[state] = None
                    self._gc_scan_state_index = (self._gc_scan_state_index + 1) % 2
                    visited_states += 1
                    continue

                for object_key, modified_at in page:
                    scanned += 1
                    self._gc_start_after[state] = object_key
                    logical_key = self._logical_key_from_object(state, object_key)
                    if logical_key is None or logical_key in seen:
                        continue
                    if modified_at <= older_than:
                        seen.add(logical_key)
                        candidates.append(ArtifactRef(logical_key))
                        if len(candidates) >= max_results or scanned >= max_scan:
                            break

                if not is_truncated:
                    self._gc_start_after[state] = None
                    self._gc_scan_state_index = (self._gc_scan_state_index + 1) % 2
                    visited_states += 1
        return tuple(candidates)

    def has_run_ref(self, ref: ArtifactRef) -> bool:
        if not ref.key.startswith("runs/"):
            raise ArtifactContractError("GC may inspect only the runs namespace")
        return any(
            self._head(ArtifactRef(ref.key, state=state)) is not None
            for state in (ArtifactState.TEMPORARY, ArtifactState.READY)
        )

    def is_stale_run_ref(self, ref: ArtifactRef, *, older_than: datetime) -> bool:
        if not ref.key.startswith("runs/"):
            raise ArtifactContractError("GC may inspect only the runs namespace")
        if older_than.tzinfo is None or older_than.utcoffset() is None:
            raise ValueError("older_than must be timezone-aware")

        exists = False
        for state in (ArtifactState.TEMPORARY, ArtifactState.READY):
            response = self._head(ArtifactRef(ref.key, state=state))
            if response is None:
                continue
            exists = True
            raw_modified = response.headers.get("last-modified")
            if raw_modified is None:
                return False
            try:
                modified_at = parsedate_to_datetime(raw_modified)
            except (TypeError, ValueError):
                return False
            if modified_at.tzinfo is None:
                modified_at = modified_at.replace(tzinfo=UTC)
            if modified_at > older_than:
                return False
        return exists

    def _stat_optional(self, ref: ArtifactRef) -> ArtifactStat | None:
        try:
            return self.stat(ref)
        except KeyError:
            return None

    @staticmethod
    def _require_same_artifact(
        temporary: ArtifactStat,
        ready: ArtifactStat,
    ) -> None:
        if (
            temporary.size_bytes != ready.size_bytes
            or temporary.checksum != ready.checksum
            or temporary.content_type != ready.content_type
        ):
            raise ArtifactContractError(
                "ready artifact already exists with different content"
            )

    def _head(self, ref: ArtifactRef) -> httpx.Response | None:
        response = self._request("HEAD", ref, payload_hash=_EMPTY_SHA256)
        if response.status_code == 404:
            return None
        self._raise_for_status(response, operation="stat", ref=ref)
        return response

    def _list_run_objects(
        self,
        state: ArtifactState,
        *,
        start_after: str | None,
        max_keys: int,
    ) -> tuple[list[tuple[str, datetime]], bool]:
        state_prefix = self._object_prefix(state, "runs/")
        query = {
            "list-type": "2",
            "max-keys": str(max_keys),
            "prefix": state_prefix,
        }
        if start_after is not None:
            query["start-after"] = start_after
        response = self._request_bucket(
            "GET",
            query=query,
            payload_hash=_EMPTY_SHA256,
        )
        self._raise_for_status(response, operation="list", ref=None)
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise S3ArtifactStoreError("S3 list response is not valid XML") from exc

        result: list[tuple[str, datetime]] = []
        for item in root.findall("{*}Contents"):
            key = item.findtext("{*}Key")
            modified = item.findtext("{*}LastModified")
            if not key or not modified:
                continue
            try:
                modified_at = datetime.fromisoformat(
                    modified.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise S3ArtifactStoreError(
                    "S3 list returned invalid LastModified"
                ) from exc
            result.append((key, modified_at))
        is_truncated = (
            root.findtext("{*}IsTruncated", default="false").lower() == "true"
        )
        return result, is_truncated

    def _logical_key_from_object(
        self,
        state: ArtifactState,
        object_key: str,
    ) -> str | None:
        prefix = self._object_prefix(state, "")
        if not object_key.startswith(prefix):
            return None
        logical_key = object_key[len(prefix) :]
        if not logical_key.startswith("runs/"):
            return None
        return logical_key

    def _request(
        self,
        method: str,
        ref: ArtifactRef,
        *,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        content: BinaryIO | bytes | None = None,
        payload_hash: str,
    ) -> httpx.Response:
        request = self._build_request(
            method,
            ref,
            headers=headers,
            query=query,
            content=content,
            payload_hash=payload_hash,
        )
        try:
            return self._client.send(request)
        except httpx.HTTPError as exc:
            raise S3ArtifactStoreError(
                f"S3 {method} failed for {ref.key}"
            ) from exc

    def _request_bucket(
        self,
        method: str,
        *,
        query: Mapping[str, str] | None = None,
        payload_hash: str,
    ) -> httpx.Response:
        request = self._build_signed_request(
            method,
            object_key=None,
            headers=None,
            query=query,
            content=None,
            payload_hash=payload_hash,
        )
        try:
            return self._client.send(request)
        except httpx.HTTPError as exc:
            raise S3ArtifactStoreError(
                f"S3 {method} failed for bucket {self._bucket}"
            ) from exc

    def _build_request(
        self,
        method: str,
        ref: ArtifactRef,
        *,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, str] | None = None,
        content: BinaryIO | bytes | None = None,
        payload_hash: str,
    ) -> httpx.Request:
        return self._build_signed_request(
            method,
            object_key=self._object_key(ref),
            headers=headers,
            query=query,
            content=content,
            payload_hash=payload_hash,
        )

    def _build_signed_request(
        self,
        method: str,
        *,
        object_key: str | None,
        headers: Mapping[str, str] | None,
        query: Mapping[str, str] | None,
        content: BinaryIO | bytes | None,
        payload_hash: str,
    ) -> httpx.Request:
        now = self._clock().astimezone(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        short_date = now.strftime("%Y%m%d")
        canonical_uri, url = self._url(object_key)
        canonical_query = self._canonical_query(query or {})
        if canonical_query:
            url = f"{url}?{canonical_query}"

        signing_headers = {
            key.lower(): value for key, value in (headers or {}).items()
        }
        signing_headers["host"] = urlsplit(url).netloc
        signing_headers["x-amz-content-sha256"] = payload_hash
        signing_headers["x-amz-date"] = amz_date
        if self._session_token is not None:
            signing_headers["x-amz-security-token"] = self._session_token

        canonical_headers, signed_headers = self._canonical_headers(
            signing_headers
        )
        canonical_request = "\n".join(
            (
                method.upper(),
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        credential_scope = (
            f"{short_date}/{self._region}/s3/aws4_request"
        )
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(
                    canonical_request.encode("utf-8")
                ).hexdigest(),
            )
        )
        signing_key = self._signing_key(short_date)
        signature = hmac.new(
            signing_key,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signing_headers["authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return self._client.build_request(
            method,
            url,
            headers=signing_headers,
            content=content,
        )

    def _url(self, object_key: str | None) -> tuple[str, str]:
        if self._force_path_style:
            segments = [self._bucket]
            if object_key is not None:
                segments.extend(object_key.split("/"))
            canonical_uri = "/" + "/".join(
                quote(segment, safe="-_.~") for segment in segments
            )
            netloc = self._endpoint.netloc
        else:
            netloc = f"{self._bucket}.{self._endpoint.netloc}"
            canonical_uri = "/"
            if object_key is not None:
                canonical_uri += "/".join(
                    quote(segment, safe="-_.~")
                    for segment in object_key.split("/")
                )
        url = urlunsplit(
            (self._endpoint.scheme, netloc, canonical_uri, "", "")
        )
        return canonical_uri, url

    @staticmethod
    def _canonical_query(query: Mapping[str, str]) -> str:
        pairs = []
        for key, value in sorted(query.items()):
            pairs.append(
                f"{quote(str(key), safe='-_.~')}="
                f"{quote(str(value), safe='-_.~')}"
            )
        return "&".join(pairs)

    @staticmethod
    def _canonical_headers(
        headers: Mapping[str, str],
    ) -> tuple[str, str]:
        normalized: list[tuple[str, str]] = []
        for key, value in headers.items():
            name = key.strip().lower()
            normalized_value = _SPACE_RE.sub(" ", str(value).strip())
            normalized.append((name, normalized_value))
        normalized.sort(key=lambda item: item[0])
        canonical = "".join(
            f"{key}:{value}\n" for key, value in normalized
        )
        signed = ";".join(key for key, _ in normalized)
        return canonical, signed

    def _signing_key(self, short_date: str) -> bytes:
        date_key = hmac.new(
            f"AWS4{self._secret_key}".encode(),
            short_date.encode(),
            hashlib.sha256,
        ).digest()
        region_key = hmac.new(
            date_key,
            self._region.encode(),
            hashlib.sha256,
        ).digest()
        service_key = hmac.new(
            region_key,
            b"s3",
            hashlib.sha256,
        ).digest()
        return hmac.new(
            service_key,
            b"aws4_request",
            hashlib.sha256,
        ).digest()

    def _object_key(self, ref: ArtifactRef) -> str:
        return self._object_prefix(ref.state, ref.key)

    def _object_prefix(self, state: ArtifactState, suffix: str) -> str:
        parts = []
        if self._prefix:
            parts.append(self._prefix)
        parts.append(state.value)
        base = "/".join(parts) + "/"
        return base + suffix

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
        *,
        operation: str,
        ref: ArtifactRef | None,
    ) -> None:
        embedded_error = (
            response.status_code == 200
            and response.content.lstrip().startswith(b"<Error")
        )
        if response.status_code < 400 and not embedded_error:
            return
        target = ref.key if ref is not None else "bucket"
        detail = response.text[:500].strip()
        raise S3ArtifactStoreError(
            f"S3 {operation} failed for {target}: "
            f"HTTP {response.status_code}"
            + (f" {detail}" if detail else "")
        )
